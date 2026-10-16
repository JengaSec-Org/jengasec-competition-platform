"""The machine endpoints the range calls.

The machine endpoints are guarded by one shared secret rather than a session,
because the caller is the shared VM's puller and has no login. The same
three choices as `config/tasks.py`, for the same reasons: 404 when the
token is unset so a deployment that forgot the variable exposes nothing,
`compare_digest` so a wrong token leaks nothing through timing, and POST
only for anything that writes.

    GET  /deployments/api/pending/   what to pull, including the token
    POST /deployments/api/status/    one of the four checks, done or failed
    GET  /deployments/api/range-exports/ current cell and scope exports

`pending` returns a credential, which is why it is on the same guard and
audited. It exists so the token travels machine to machine over HTTPS on
the functions VLAN rather than through a person and a chat window.
"""
import json
import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from audit.models import AuditLog
from accounts.models import Team
from competitions.constants import Track
from competitions.models import Competition
from cyberrange.models import WireGuardProfile
from services import deployment_service as deploy
from services import range_service
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
    # A team that is not ready (no cell, no reserved IP) is reported, not fatal:
    # one unready team must not stop every other team's deployment.
    blocked = []
    for row in rows:
        try:
            namespace = deploy.namespace_for(row.team)
            service_ip = deploy.service_ip_for(row.team)
        except ValueError as exc:
            blocked.append({"id": row.pk, "team": row.team.team_identifier or row.team.team_name,
                            "error": str(exc)})
            continue
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
    response = JsonResponse({"pending": payload, "blocked": blocked}, json_dumps_params={"indent": None})
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
        if len(request.body) > 65536:
            return JsonResponse({"error": "body too large"}, status=413)
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

    try:
        deploy.record_status(
            submission,
            new_status,
            detail=detail.strip(),
            scan_report=body.get("scan_report"),
            logs_excerpt=logs_excerpt,
            resolved_digest=body.get("resolved_digest"),
            extra_digests=body.get("extra_digests"),
        )
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
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
        if any(deploy.valid_pinned_reference(value) for value in
               AppSubmission.objects.filter(team=team, frozen_at__isnull=False)
               .values_list("frozen_digest", flat=True)):
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
    extra_digests = body.get("extra_digests", [])
    try:
        submission = deploy.record_freeze(submission, digest, extra_digests)
    except deploy.FreezeConflict as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
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
            frozen = list(AppSubmission.objects.filter(
                team=team, frozen_at__isnull=False,
            ))
            frozen = [row for row in frozen if deploy.valid_pinned_reference(row.frozen_digest)]
            if len(frozen) > 1:
                return JsonResponse({"error": "team has ambiguous frozen applications"}, status=409)
            passing = frozen[0] if frozen else None
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
        extras = row.get("extra_digests", [])
        if (not isinstance(extras, list) or len(extras) > deploy.MAX_EXTRA_IMAGES
                or any(not deploy.valid_digest(value) for value in extras)):
            return JsonResponse({"error": "extra_digests must be observed sha256 hashes"}, status=400)
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
            observed = row.get("image_digest", "")
            if deploy.valid_pinned_reference(observed):
                observed = observed.rsplit("@", 1)[1]
            if deploy.valid_digest(observed):
                frozen = [item for item in AppSubmission.objects.filter(
                    team_id=row["team_id"], frozen_at__isnull=False,
                ) if item.frozen_verified_digest or deploy.valid_pinned_reference(item.frozen_digest)]
                if len(frozen) == 1:
                    # The runner observed the pod image; availability is a
                    # separate measurement, including when this cell is down.
                    try:
                        deploy.record_artifact(frozen[0], observed, row.get("extra_digests"))
                    except ValueError:
                        # Runtime health is still useful when the runner has
                        # no complete proof for the frozen extra containers.
                        logger.warning("Artifact confirmation incomplete for submission %s", frozen[0].pk)
        for row in peers:
            profile = profile_rows[row["id"]]
            profile.active = row["active"]
            profile.last_handshake_at = row.get("_parsed_handshake")
            profile.save(update_fields=["active", "last_handshake_at"])

    return JsonResponse({"updated_cells": len(cells), "updated_peers": len(peers)})


@never_cache
@require_GET
def range_exports(request):
    """Return competition-specific cell and scope exports to the range runner."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal

    competition_id = request.GET.get("competition")
    try:
        competition = Competition.objects.get(pk=int(competition_id))
    except (TypeError, ValueError):
        return JsonResponse({"error": "competition must be an integer"}, status=400)
    except Competition.DoesNotExist:
        return JsonResponse({"error": "unknown competition"}, status=404)

    try:
        payload = {
            "cells_yaml": range_service.cells_yaml(competition),
            "scope_csv": range_service.scope_csv(competition),
        }
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=409)

    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store"
    return response


@never_cache
@require_GET
def active(request):
    """Bounded artifact checks. This response never contains registry credentials."""
    refusal = _authorise(request)
    if refusal is not None:
        return refusal
    try:
        after = int(request.GET.get("after", "0"))
        if after < 0:
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "invalid cursor"}, status=400)
    rows = list(AppSubmission.objects.filter(pk__gt=after, frozen_at__isnull=False).filter(
        Q(frozen_verified_digest__regex=r"^sha256:[0-9a-f]{64}$")
        | Q(frozen_digest__regex=r"^[^@\s]+@sha256:[0-9a-f]{64}$"))
                .select_related("team").order_by("pk")[:100])
    payload = []
    for row in rows:
        frozen_digest = row.frozen_verified_digest
        if not frozen_digest and deploy.valid_pinned_reference(row.frozen_digest):
            frozen_digest = row.frozen_digest.rsplit("@", 1)[1]
        if not frozen_digest:
            continue
        try:
            namespace = deploy.namespace_for(row.team)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=409)
        payload.append({"id": row.pk, "enterprise": row.team.enterprise,
                        "cell": row.team.cell_id, "namespace": namespace,
                        "frozen_verified_digest": frozen_digest,
                        "frozen_digest": row.frozen_digest,
                        "frozen_extra_images": row.frozen_extra_images or []})
    return JsonResponse({"active": payload, "next_cursor": rows[-1].pk if rows else after})


@csrf_exempt
@never_cache
@require_POST
def artifact(request):
    refusal = _authorise(request)
    if refusal is not None:
        return refusal
    try:
        if len(request.body) > 4096:
            return JsonResponse({"error": "body too large"}, status=413)
        body = json.loads(request.body)
        if (not isinstance(body, dict) or not {"id", "resolved_digest"} <= set(body)
                or set(body) - {"id", "resolved_digest", "extra_digests"} or type(body["id"]) is not int):
            raise ValueError("Expected an id and verified digest.")
        row = AppSubmission.objects.get(pk=body["id"])
        row = deploy.record_artifact(row, body["resolved_digest"], body.get("extra_digests"))
    except (ValueError, TypeError, AppSubmission.DoesNotExist) as error:
        return JsonResponse({"error": "invalid artifact receipt"}, status=400)
    return JsonResponse({"id": row.pk, "matches_frozen": row.resolved_digest == row.frozen_verified_digest})
