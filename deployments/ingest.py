"""The machine endpoints the range calls.

Two of them, both guarded by one shared secret rather than a session,
because the caller is the shared VM's puller and has no login. The same
three choices as `config/tasks.py`, for the same reasons: 404 when the
token is unset so a deployment that forgot the variable exposes nothing,
`compare_digest` so a wrong token leaks nothing through timing, and POST
only for anything that writes.

    GET  /deployments/api/pending/   what to pull, including the token
    POST /deployments/api/status/    one of the four checks, done or failed

`pending` returns a credential, which is why it is on the same guard and
audited. It exists so the token travels machine to machine over HTTPS on
the functions VLAN rather than through a person and a chat window.
"""
import json
import logging
import secrets

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from audit.models import AuditLog
from services import deployment_service as deploy
from services.audit_service import record as audit

from .models import AppSubmission

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-Deploy-Token"


def _configured_token():
    return (getattr(settings, "DEPLOY_TOKEN", "") or "").strip()


def _authorise(request):
    """None when the caller may proceed, otherwise the response to return."""
    expected = _configured_token()
    if not expected:
        raise Http404("Not found")
    offered = request.headers.get(TOKEN_HEADER, "")
    if not offered or not secrets.compare_digest(offered, expected):
        logger.warning("Deployment endpoint rejected a request with a bad or missing token.")
        return JsonResponse({"error": "forbidden"}, status=403)
    return None


@csrf_exempt
@never_cache
@require_GET
def pending(request):
    """Attempts still waiting on a check, with what is needed to pull them."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal

    rows = (
        AppSubmission.objects.filter(status=AppSubmission.Status.SUBMITTED)
        .select_related("team", "competition")
        .order_by("created_at")[:100]
    )
    payload = []
    for row in rows:
        payload.append({
            "id": row.pk,
            "competition": row.competition_id,
            "enterprise": row.team.enterprise,
            "cell": row.team.cell_id,
            "team": row.team.team_identifier or row.team.team_name,
            "attempt": row.attempt,
            "image_reference": row.image_reference,
            "registry_username": row.registry_username,
            "pull_token": row.reveal_pull_token(),
            "port": row.port,
            "health_path": row.health_path,
            "needs_database": row.needs_database,
            "env_vars": row.env_vars or {},
            "extra_images": row.extra_images or [],
        })
    if payload:
        audit(
            AuditLog.Action.APP_TOKEN_REVEALED,
            request=request,
            actor=None,
            description=f"Range collected {len(payload)} pending submission(s)",
            submissions=[row["id"] for row in payload],
        )
    response = JsonResponse({"pending": payload}, json_dumps_params={"indent": None})
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@never_cache
@require_POST
def status(request):
    """Record one check. Body: {id, status, detail, scan_report, logs_excerpt}."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)

    try:
        submission = AppSubmission.objects.get(pk=body.get("id"))
    except (AppSubmission.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "unknown submission"}, status=404)

    new_status = body.get("status", "")
    if new_status not in AppSubmission.Status.values:
        return JsonResponse(
            {"error": "unknown status", "allowed": list(AppSubmission.Status.values)},
            status=400,
        )

    deploy.record_status(
        submission,
        new_status,
        detail=(body.get("detail") or "").strip(),
        scan_report=body.get("scan_report"),
        logs_excerpt=body.get("logs_excerpt"),
    )
    audit(
        AuditLog.Action.APP_STATUS_CHANGED,
        request=request,
        actor=None,
        target=submission,
        description=f"Range reported {submission.get_status_display()}",
    )
    return JsonResponse({
        "id": submission.pk,
        "status": submission.status,
        "is_passing": submission.is_passing,
    })
