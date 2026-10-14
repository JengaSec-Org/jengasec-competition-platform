"""Bounded machine-input validation shared by evidence and score ingestion."""
import json
import secrets
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import Http404
from django.utils import timezone

from competitions import grading
from jengasec_scoring_policy import POLICY_SHA256, POLICY_VERSION, PolicyError, calculate
from scoring.models import ScoringPolicyEnrollment

MAX_BODY_BYTES = 1024 * 1024
MAX_BATCH_ROWS = 100
SNAPSHOT_KEYS = {"policy_version", "side", "criterion", "window", "facts", "quality",
                 "assignment_revisions"}


class ProtocolError(ValueError):
    def __init__(self, message, *, code="invalid", retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def authenticated(request):
    expected = (getattr(settings, "FEED_TOKEN", "") or "").strip()
    if not expected:
        raise Http404("Not found")
    offered = request.headers.get("X-Feed-Token", "")
    return bool(offered) and secrets.compare_digest(offered.encode(), expected.encode())


def read_json(request):
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("invalid content length") from exc
    if length < 0 or length > MAX_BODY_BYTES:
        raise ProtocolError("request exceeds one MiB", code="too_large")
    if request.content_type != "application/json":
        raise ProtocolError("Content-Type must be application/json")
    try:
        raw = request.read(MAX_BODY_BYTES + 1)
    except RequestDataTooBig as exc:
        raise ProtocolError("request exceeds one MiB", code="too_large") from exc
    if len(raw) > MAX_BODY_BYTES:
        raise ProtocolError("request exceeds one MiB", code="too_large")
    try:
        body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ProtocolError("body must be finite JSON") from exc
    if not isinstance(body, dict):
        raise ProtocolError("body must be a JSON object")
    return body


def timestamp(value, name):
    if not isinstance(value, str) or len(value) > 40:
        raise ProtocolError(f"{name} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError(f"{name} must be a UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ProtocolError(f"{name} must be a UTC timestamp")
    return parsed.astimezone(dt_timezone.utc)


def enrollment_for(competition, *, lock=False):
    query = ScoringPolicyEnrollment.objects
    if lock:
        query = query.select_for_update()
    enrollment = query.filter(competition=competition).first()
    if not enrollment:
        raise ProtocolError("competition is not enrolled in automatic scoring", code="not_enrolled")
    if enrollment.policy_version != POLICY_VERSION or enrollment.policy_sha256 != POLICY_SHA256:
        raise ProtocolError("enrolled policy is unavailable or changed", code="policy_mismatch")
    if grading.check():
        raise ProtocolError("published grading criteria disagree with the frozen policy", code="policy_mismatch")
    return enrollment


def validate_snapshot(competition, team, snapshot, *, enrollment=None, identity=True):
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_KEYS:
        raise ProtocolError("invalid input snapshot fields")
    if snapshot["side"] != grading.side_key(team):
        raise ProtocolError("snapshot side does not match the canonical team")
    if snapshot["policy_version"] != POLICY_VERSION:
        raise ProtocolError("unsupported policy version")
    window = snapshot["window"]
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise ProtocolError("window requires start and end")
    start, end = timestamp(window["start"], "window start"), timestamp(window["end"], "window end")
    enrollment = enrollment or enrollment_for(competition)
    if not start < end or start != enrollment.window_start or end > enrollment.window_end:
        raise ProtocolError("window must be cumulative from the enrolled start within its end")
    if end > timezone.now() - timezone.timedelta(seconds=120):
        raise ProtocolError("delivery grace has not elapsed", code="grace_period", retryable=True)
    revisions = snapshot["assignment_revisions"]
    if not isinstance(revisions, list) or not 1 <= len(revisions) <= 1000 or any(
        type(r) is not int or r < 1 for r in revisions
    ) or len(set(revisions)) != len(revisions):
        raise ProtocolError("assignment_revisions must be distinct positive integers")
    if identity:
        try:
            from services.scoring_identity import IdentityError, validate_scoring_assignment
        except ImportError as exc:
            raise ProtocolError("historical identity interface unavailable",
                                code="identity_unavailable", retryable=True) from exc
        try:
            validate_scoring_assignment(competition, team, start, end, revisions)
        except IdentityError as exc:
            raise ProtocolError(str(exc), code="identity_unavailable", retryable=True) from exc
    try:
        result = calculate(snapshot["side"], snapshot["criterion"], snapshot["facts"],
                           snapshot["quality"], policy_version=snapshot["policy_version"])
    except PolicyError as exc:
        raise ProtocolError(str(exc)) from exc
    return result, start, end
