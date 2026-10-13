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
import re
import secrets

from django.conf import settings
from django.db import transaction
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from audit.models import AuditLog
from accounts.models import Team
from competitions.constants import Track
from cyberrange.models import WireGuardProfile
from services import deployment_service as deploy
from services.audit_service import record as audit

from .models import AppRuntimeStatus, AppSubmission

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
        try:
            namespace = deploy.namespace_for(row.team)
            service_ip = deploy.service_ip_for(row.team)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        payload.append({
            "id": row.pk,
            "competition": row.competition_id,
            "enterprise": row.team.enterprise,
            "cell": row.team.cell_id,
            "namespace": namespace,
            "service_ip": service_ip,
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
    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

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
    detail = body.get("detail", "")
    logs_excerpt = body.get("logs_excerpt")
    if not isinstance(detail, str) or (logs_excerpt is not None and not isinstance(logs_excerpt, str)):
        return JsonResponse({"error": "detail and logs_excerpt must be strings"}, status=400)

    deploy.record_status(
        submission,
        new_status,
        detail=detail.strip(),
        scan_report=body.get("scan_report"),
        logs_excerpt=logs_excerpt,
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


@never_cache
@require_GET
def freeze_candidates(request):
    """Return each application team's last passing attempt for registry copy."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal

    teams = Team.objects.filter(
        team_type=Team.TeamType.BLUE,
        track=Track.APPLICATION,
        status=Team.Status.APPROVED,
    ).select_related("competition").order_by(
        "competition_id", "enterprise", "cell_id"
    )
    competition_id = request.GET.get("competition")
    if not competition_id:
        return JsonResponse({"error": "competition is required"}, status=400)
    try:
        teams = teams.filter(competition_id=int(competition_id))
    except (TypeError, ValueError):
        return JsonResponse({"error": "competition must be an integer"}, status=400)

    candidates, missing = [], []
    for team in teams:
        passing = deploy.last_passing(team)
        label = team.cell_label or team.team_name
        if passing is None:
            missing.append({"team": team.pk, "cell": label})
            continue
        if passing.frozen_digest:
            continue
        try:
            namespace = deploy.namespace_for(team)
            service_ip = deploy.service_ip_for(team)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        candidates.append({
            "id": passing.pk,
            "competition": passing.competition_id,
            "team_id": team.pk,
            "enterprise": team.enterprise,
            "cell": team.cell_id,
            "namespace": namespace,
            "service_ip": service_ip,
            "team": team.team_identifier or team.team_name,
            "attempt": passing.attempt,
            "image_reference": passing.image_reference,
            "registry_username": passing.registry_username,
            "pull_token": passing.reveal_pull_token(),
            "port": passing.port,
            "health_path": passing.health_path,
            "needs_database": passing.needs_database,
            "env_vars": passing.env_vars or {},
            "extra_images": passing.extra_images or [],
        })

    if candidates:
        audit(
            AuditLog.Action.APP_TOKEN_REVEALED,
            request=request,
            actor=None,
            description=f"Range collected {len(candidates)} freeze candidate(s)",
            submissions=[row["id"] for row in candidates],
        )
    response = JsonResponse({"candidates": candidates, "missing": missing})
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@never_cache
@require_POST
def freeze_submission(request):
    """Record the JengaSec-registry digest after an image is copied."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

    try:
        submission = AppSubmission.objects.select_related("team").get(pk=body.get("id"))
    except (AppSubmission.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": "unknown submission"}, status=404)

    digest = body.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(
        r"[^@\s]+@sha256:[0-9a-f]{64}", digest
    ):
        return JsonResponse(
            {"error": "digest must be a registry image reference ending in @sha256:<64 hex>"},
            status=400,
        )
    extra_digests = body.get("extra_digests", [])
    if (
        not isinstance(extra_digests, list)
        or len(extra_digests) != len(submission.extra_images or [])
        or any(
            not isinstance(item, str)
            or not re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", item)
            for item in extra_digests
        )
    ):
        return JsonResponse(
            {"error": "extra_digests must contain one pinned digest per submitted extra image"},
            status=400,
        )
    last_passing = deploy.last_passing(submission.team)
    if not submission.is_passing or last_passing is None or last_passing.pk != submission.pk:
        return JsonResponse({"error": "submission is not the team's last passing attempt"}, status=409)
    if submission.frozen_digest:
        if (
            submission.frozen_digest != digest
            or submission.frozen_extra_images != extra_digests
        ):
            return JsonResponse({"error": "submission is already frozen to another digest"}, status=409)
        return JsonResponse({
            "id": submission.pk,
            "digest": digest,
            "extra_digests": extra_digests,
            "frozen": True,
        })

    with transaction.atomic():
        submission = AppSubmission.objects.select_for_update().get(pk=submission.pk)
        if submission.frozen_digest and (
            submission.frozen_digest != digest
            or submission.frozen_extra_images != extra_digests
        ):
            return JsonResponse({"error": "submission is already frozen to another digest"}, status=409)
        submission.frozen_digest = digest
        submission.frozen_extra_images = extra_digests
        submission.frozen_at = submission.frozen_at or timezone.now()
        submission.save(update_fields=["frozen_digest", "frozen_extra_images", "frozen_at"])
    audit(
        AuditLog.Action.APP_FROZEN,
        request=request,
        actor=None,
        target=submission,
        description=f"Range pinned {submission.team.cell_label or submission.team.team_name} by digest",
    )
    return JsonResponse({
        "id": submission.pk,
        "digest": digest,
        "extra_digests": extra_digests,
        "frozen": True,
    })


@csrf_exempt
@never_cache
def range_state(request):
    """Exchange frozen application definitions and live range observations."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal

    if request.method == "GET":
        teams = Team.objects.filter(
            team_type=Team.TeamType.BLUE,
            track=Track.APPLICATION,
            status=Team.Status.APPROVED,
        ).order_by(
            "competition_id", "enterprise", "cell_id"
        )
        competition_id = request.GET.get("competition")
        if competition_id:
            try:
                teams = teams.filter(competition_id=int(competition_id))
            except (TypeError, ValueError):
                return JsonResponse({"error": "competition must be an integer"}, status=400)
        cells = []
        for team in teams:
            passing = deploy.last_passing(team)
            runtime = getattr(team, "app_runtime_status", None)
            try:
                namespace = deploy.namespace_for(team)
                service_ip = deploy.service_ip_for(team)
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=409)
            cells.append({
                "team_id": team.pk,
                "competition": team.competition_id,
                "enterprise": team.enterprise,
                "cell": team.cell_id,
                "namespace": namespace,
                "service_ip": service_ip,
                "team": team.team_identifier or team.team_name,
                "image_digest": (
                    passing.frozen_digest if passing and passing.frozen_at else ""
                ),
                "port": passing.port if passing and passing.frozen_at else None,
                "health_path": passing.health_path if passing and passing.frozen_at else "",
                "needs_database": (
                    passing.needs_database if passing and passing.frozen_at else False
                ),
                "env_vars": passing.env_vars if passing and passing.frozen_at else {},
                "extra_digests": (
                    passing.frozen_extra_images if passing and passing.frozen_at else []
                ),
                "runtime": {
                    "status": runtime.status,
                    "healthy": runtime.healthy,
                    "image_digest": runtime.observed_image_digest,
                    "detail": runtime.detail,
                    "checked_at": runtime.checked_at.isoformat() if runtime.checked_at else None,
                } if runtime else None,
            })
        peer_profiles = WireGuardProfile.objects.filter(
            revoked=False, team__isnull=False
        )
        if competition_id:
            peer_profiles = peer_profiles.filter(team__competition_id=int(competition_id))
        peers = [{
            "id": profile.pk,
            "team_id": profile.team_id,
            "address": profile.address,
            "revoked": profile.revoked,
        } for profile in peer_profiles.order_by("pk")]
        response = JsonResponse({"cells": cells, "peers": peers})
        response["Cache-Control"] = "no-store"
        return response

    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "body is not JSON"}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": "body must be a JSON object"}, status=400)

    cells = body.get("cells", [])
    peers = body.get("peers", [])
    if not isinstance(cells, list) or not isinstance(peers, list):
        return JsonResponse({"error": "cells and peers must be arrays"}, status=400)
    if len(cells) > 1000 or len(peers) > 10000:
        return JsonResponse({"error": "too many range observations"}, status=400)

    cell_ids, peer_ids = [], []
    for row in cells:
        if (
            not isinstance(row, dict)
            or type(row.get("team_id")) is not int
        ):
            return JsonResponse({"error": "each cell needs an integer team_id"}, status=400)
        if row.get("status") not in AppRuntimeStatus.Status.values:
            return JsonResponse({"error": "unknown cell status"}, status=400)
        if not isinstance(row.get("healthy"), bool):
            return JsonResponse({"error": "each cell needs a boolean healthy value"}, status=400)
        if (
            not isinstance(row.get("image_digest", ""), str)
            or not isinstance(row.get("detail", ""), str)
        ):
            return JsonResponse({"error": "cell digest and detail must be strings"}, status=400)
        row["_checked_at"] = timezone.now()
        cell_ids.append(row["team_id"])
    for row in peers:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            return JsonResponse({"error": "each peer needs an integer id"}, status=400)
        if not isinstance(row.get("active"), bool):
            return JsonResponse({"error": "each peer needs a boolean active value"}, status=400)
        handshake = row.get("last_handshake_at")
        if handshake is not None:
            parsed = parse_datetime(handshake) if isinstance(handshake, str) else None
            if parsed is None or timezone.is_naive(parsed):
                return JsonResponse(
                    {"error": "last_handshake_at must be an ISO-8601 timezone-aware timestamp"},
                    status=400,
                )
            row["_parsed_handshake"] = parsed
        peer_ids.append(row["id"])
    if len(cell_ids) != len(set(cell_ids)) or len(peer_ids) != len(set(peer_ids)):
        return JsonResponse({"error": "duplicate cell or peer ids"}, status=400)
    if any(
        len(row.get("image_digest", "")) > 500 or len(row.get("detail", "")) > 4000
        for row in cells
    ):
        return JsonResponse({"error": "cell digest or detail is too long"}, status=400)

    with transaction.atomic():
        team_rows = {
            team.pk: team for team in Team.objects.filter(
                pk__in=cell_ids,
                team_type=Team.TeamType.BLUE,
                track=Track.APPLICATION,
                status=Team.Status.APPROVED,
            )
        }
        profile_rows = {
            profile.pk: profile for profile in WireGuardProfile.objects.filter(
                pk__in=peer_ids, revoked=False
            )
        }
        if len(team_rows) != len(cell_ids) or len(profile_rows) != len(peer_ids):
            return JsonResponse({"error": "unknown or ineligible team/peer id"}, status=404)
        for row in cells:
            runtime, _ = AppRuntimeStatus.objects.get_or_create(
                team=team_rows[row["team_id"]]
            )
            runtime.status = row["status"]
            runtime.healthy = row["healthy"]
            runtime.observed_image_digest = row.get("image_digest", "")
            runtime.detail = row.get("detail", "")[:4000]
            runtime.checked_at = row["_checked_at"]
            runtime.save(update_fields=[
                "status", "healthy", "observed_image_digest", "detail", "checked_at",
            ])
        for row in peers:
            profile = profile_rows[row["id"]]
            profile.active = row["active"]
            profile.last_handshake_at = row.get("_parsed_handshake")
            profile.save(update_fields=["active", "last_handshake_at"])

    return JsonResponse({"updated_cells": len(cells), "updated_peers": len(peers)})
