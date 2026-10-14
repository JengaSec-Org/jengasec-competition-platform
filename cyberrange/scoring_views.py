"""Bounded machine interfaces for scoring sources, with separate credentials."""
import hmac
import json

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from competitions.models import Competition
from services import scoring_identity, scoring_scenarios

MAX_REQUEST_BYTES = 65536
MAX_RESPONSE_BYTES = 1024 * 1024


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value


def _error(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _feed_auth(request):
    expected = getattr(settings, "FEED_TOKEN", "") or ""
    if not expected:
        return _error("Feed is not configured.", 404)
    supplied = request.headers.get("X-Feed-Token", "")
    if not isinstance(expected, str) or not hmac.compare_digest(supplied.encode(), expected.encode()):
        return _error("Invalid feed credential.", 403)
    return None


def _identity_auth(request):
    credentials = ((getattr(settings, "SCORING_IDENTITY_TOKEN", ""), "X-Scoring-Identity-Token"),
                   (getattr(settings, "FEED_TOKEN", ""), "X-Feed-Token"))
    for expected, header in credentials:
        if isinstance(expected, str) and expected and hmac.compare_digest(
            request.headers.get(header, "").encode(), expected.encode(),
        ):
            return None
    if not any(expected for expected, _ in credentials):
        return _error("Identity feed is not configured.", 404)
    return _error("Invalid identity credential.", 403)


def _competition(request):
    value = request.GET.get("competition", "")
    if not value.isascii() or not value.isdigit() or len(value) > 12:
        raise scoring_identity.IdentityError("An explicit competition ID is required.")
    competition = Competition.objects.filter(pk=int(value)).first()
    if competition is None:
        raise scoring_identity.IdentityError("Competition is unknown.")
    return competition


def _time(request, name, *, required=False):
    value = request.GET.get(name)
    if value is None and not required:
        return None
    return scoring_scenarios._timestamp(value, name)


def _response(data):
    response = JsonResponse(data)
    if len(response.content) > MAX_RESPONSE_BYTES:
        return _error("Export is too large; reduce the interval or limit.", 413)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def identity_snapshot(request):
    denied = _identity_auth(request)
    if denied is not None:
        return denied
    try:
        data = scoring_identity.identity_snapshot(_competition(request), start=_time(request, "start"), end=_time(request, "end"))
        return _response(data)
    except (scoring_identity.IdentityError, scoring_scenarios.ScenarioError) as exc:
        return _error(str(exc), 409)


@require_GET
def scenario_snapshot(request):
    denied = _feed_auth(request)
    if denied is not None:
        return denied
    try:
        data = scoring_scenarios.scenario_snapshot(_competition(request), start=_time(request, "start", required=True), end=_time(request, "end", required=True))
        return _response(data)
    except (scoring_identity.IdentityError, scoring_scenarios.ScenarioError) as exc:
        return _error(str(exc), 409)


@csrf_exempt
def ingest_receipt(request):
    if request.method == "GET":
        denied = _feed_auth(request)
        if denied is not None:
            return denied
        try:
            data = scoring_scenarios.receipt_snapshot(_competition(request),
                after=int(request.GET.get("after", "0")), cases_after=int(request.GET.get("cases_after", "0")),
                limit=int(request.GET.get("limit", "1000")))
            return _response(data)
        except (ValueError, scoring_identity.IdentityError, scoring_scenarios.ScenarioError) as exc:
            return _error(str(exc), 400)
    if request.method != "POST":
        response = _error("Use GET or POST.", 405)
        response["Allow"] = "GET, POST"
        return response
    try:
        length = int(request.META.get("CONTENT_LENGTH", "0") or "0")
        if length < 0 or length > MAX_REQUEST_BYTES:
            return _error("Receipt is too large.", 413)
        body = request.read(MAX_REQUEST_BYTES + 1)
        if len(body) > MAX_REQUEST_BYTES:
            return _error("Receipt is too large.", 413)
        payload = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(payload, dict):
            return _error("Receipt must be an object.")
    except (ValueError, UnicodeError):
        return _error("Receipt is not valid JSON.")
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return _error("A producer credential is required.", 403)
    try:
        producer = scoring_scenarios.authenticate_producer(payload.get("producer_id"), authorization[7:])
    except scoring_scenarios.ScenarioError:
        return _error("Invalid producer credential.", 403)
    try:
        receipt, created = scoring_scenarios.record_receipt(producer, payload)
        return _response({"receipt_id": str(receipt.receipt_id), "sequence": receipt.pk,
                          "status": "accepted" if created else "duplicate"}) if not created else JsonResponse(
            {"receipt_id": str(receipt.receipt_id), "sequence": receipt.pk, "status": "accepted"}, status=201)
    except scoring_scenarios.ReceiptConflict as exc:
        return _error(str(exc), 409)
    except (scoring_scenarios.ScenarioError, scoring_identity.IdentityError) as exc:
        return _error(str(exc), 400)
