"""Authenticated legacy marks and reproducible versioned scoring publication."""
import logging
import re

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.models import Team
from competitions import grading
from competitions.constants import ENTERPRISE_CODES
from competitions.models import Competition
from jengasec_scoring_policy import POLICY_SHA256, digest, number
from scoring.models import ScoreEvent, ScoringPolicyEnrollment, ScoringReadiness
from scoring.protocol import (MAX_BATCH_ROWS, ProtocolError, authenticated,
                              enrollment_for, read_json, timestamp, validate_snapshot)
from services import scoring_evidence, scoring_service

logger = logging.getLogger(__name__)
_ENTERPRISE_BY_CODE = {code.lower(): key for key, code in ENTERPRISE_CODES.items()}
SCHEMA_VERSION = "scoring-feed/2.0"
ROW_REQUIRED = {"team", "stage", "criterion", "key", "revision", "policy_version", "window",
                "facts", "quality", "evidence", "assignment_revisions", "marks"}


def resolve_team(competition, row, *, canonical=False):
    """Never choose an arbitrary result from an ambiguous identifier."""
    identifier = row.get("team") or ""
    if not isinstance(identifier, str):
        raise ProtocolError("team identifier must be a string")
    identifier = identifier.strip()
    if canonical:
        if not re.fullmatch(r"JS[0-9]{2}-[BR]-[0-9]{3,}", identifier):
            raise ProtocolError("a canonical team identifier is required")
        try:
            from services.scoring_identity import IdentityError, resolve_team as resolve_identity
        except ImportError as exc:
            raise ProtocolError("historical identity interface unavailable",
                                code="identity_unavailable", retryable=True) from exc
        try:
            return resolve_identity(competition, identifier)
        except IdentityError as exc:
            raise ProtocolError(str(exc)) from exc
    if identifier:
        found = list(Team.objects.filter(competition=competition,
                                        team_identifier__iexact=identifier)[:2])
        return found[0] if len(found) == 1 else None
    cell, enterprise = row.get("cell") or "", row.get("enterprise") or ""
    if not isinstance(cell, str) or not isinstance(enterprise, str):
        raise ProtocolError("cell and enterprise must be strings")
    cell, enterprise = cell.strip(), enterprise.strip().lower()
    if cell and "-" in cell and not enterprise:
        enterprise, _, cell = cell.partition("-")
    enterprise = _ENTERPRISE_BY_CODE.get(enterprise, enterprise)
    if cell and enterprise:
        found = list(Team.objects.filter(competition=competition, enterprise=enterprise,
                                        cell_id__iexact=cell)[:2])
        return found[0] if len(found) == 1 else None
    return None


def _competition(body, *, required=False):
    identifier = body.get("competition")
    if identifier is not None:
        if type(identifier) is not int or identifier < 1:
            raise ProtocolError("competition must be a positive integer")
        competition = Competition.objects.filter(pk=identifier).first()
    elif not required:
        competition = Competition.objects.filter(status__in=(
            Competition.Status.OPEN, Competition.Status.IN_PROGRESS)).order_by("-start_date").first()
    else:
        raise ProtocolError("competition is required")
    if competition is None:
        raise ProtocolError("no such competition", code="not_found")
    return competition


@transaction.atomic
def publish(competition, row):
    if not isinstance(row, dict) or not ROW_REQUIRED <= set(row) or set(row) - (ROW_REQUIRED | {"note"}):
        raise ProtocolError("invalid versioned score fields")
    team = resolve_team(competition, row, canonical=True)
    if team is None:
        raise ProtocolError("no team matched")
    Team.objects.select_for_update().get(pk=team.pk)
    if team.status != Team.Status.APPROVED:
        raise ProtocolError("team is not approved for competition scoring")
    side = grading.side_key(team)
    if side == "application_red" or row["stage"] != "main":
        raise ProtocolError("this role or stage cannot receive automatic marks")
    if not isinstance(row["criterion"], str) or row["key"] != f"main:{row['criterion']}":
        raise ProtocolError("key must be the stable main criterion key")
    revision = row["revision"]
    if type(revision) is not int or not 1 <= revision <= 9223372036854775807:
        raise ProtocolError("revision must be a positive bounded integer")
    payload_hash = digest(row)
    event = ScoreEvent.objects.select_for_update().filter(
        competition=competition, team=team, dedupe_key=row["key"]).first()
    previous = event.revisions.filter(producer_revision__isnull=False).order_by(
        "-producer_revision").first() if event else None
    if previous and revision <= previous.producer_revision:
        matching = event.revisions.filter(producer_revision=revision).first()
        if matching and matching.payload_sha256 == payload_hash:
            return {"status": "duplicate", "score_revision": matching.pk,
                    "sequence": matching.sequence, "revision": revision, "key": row["key"]}
        raise ProtocolError("producer revision is stale or has different content", code="revision_conflict")
    enrollment = enrollment_for(competition, lock=True)
    if enrollment.finalized_at or competition.settings.results_published_at:
        raise ProtocolError("automatic scoring has been finalized", code="finalized")
    if event and event.override_active:
        raise ProtocolError("judge override is active", code="override_hold", retryable=True)
    snapshot = {key: row[key] for key in (
        "policy_version", "criterion", "window", "facts", "quality", "assignment_revisions")}
    snapshot["side"] = side
    result, start, end = validate_snapshot(competition, team, snapshot, enrollment=enrollment)
    if previous and previous.window_end and end < previous.window_end:
        raise ProtocolError("a cumulative measurement window cannot move backwards")
    if result.status != "ready":
        raise ProtocolError("; ".join(result.reasons), code="pending", retryable=True)
    if number(row["marks"], "marks") != result.marks:
        raise ProtocolError("marks do not match the frozen policy calculation", code="marks_mismatch")
    scoring_evidence.resolve_references(competition, team, row["evidence"], snapshot)
    event = scoring_service.award(
        team, "main", result.marks, source="telemetry", criterion=row["criterion"],
        dedupe_key=row["key"], note=row.get("note", ""),
        metadata={"producer_revision": revision, "policy_version": row["policy_version"],
                  "policy_sha256": POLICY_SHA256, "window_start": start, "window_end": end,
                  "facts": row["facts"], "quality": row["quality"],
                  "assignment_revisions": row["assignment_revisions"], "evidence": row["evidence"],
                  "input_sha256": digest(snapshot), "payload_sha256": payload_hash})
    scoring_service.recompute(team)
    accepted = event.revisions.order_by("-sequence").first()
    _store_readiness(competition, team, row["criterion"], row["policy_version"], start, end,
                     "published", "accepted")
    return {"status": "accepted", "score_revision": accepted.pk, "sequence": accepted.sequence,
            "revision": revision, "key": row["key"], "marks": str(accepted.marks)}


def _legacy(competition, row):
    if not isinstance(row, dict):
        raise ProtocolError("score row must be an object")
    team = resolve_team(competition, row)
    if team is None:
        raise ProtocolError("no team matched")
    if "marks" not in row:
        raise ProtocolError("marks missing")
    stage = row.get("stage") or "main"
    if not isinstance(stage, str):
        raise ProtocolError("invalid stage")
    scoring_service.award(
        team, stage.strip(), row["marks"], source="telemetry",
        criterion=row.get("criterion") or "", note=row.get("note") or "",
        day=row.get("day"), dedupe_key=row.get("key") or "")
    scoring_service.recompute(team)
    return {"status": "accepted"}


def error_response(exc):
    status = {"not_found": 404, "too_large": 413, "archive_full": 507,
              "archive_unavailable": 503, "identity_unavailable": 503}.get(exc.code, 400)
    return JsonResponse({"error": str(exc), "code": exc.code, "retryable": exc.retryable}, status=status)


@csrf_exempt
@never_cache
@require_POST
def feed(request):
    if not authenticated(request):
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        body = read_json(request)
        version = body.get("schema_version")
        if version is not None and version != SCHEMA_VERSION:
            raise ProtocolError("unsupported score schema")
        v2 = version == SCHEMA_VERSION
        if v2 and set(body) != {"schema_version", "competition", "scores"}:
            raise ProtocolError("invalid versioned feed envelope")
        competition = _competition(body, required=v2)
        rows = body.get("scores")
        if not isinstance(rows, list) or len(rows) > MAX_BATCH_ROWS:
            raise ProtocolError("scores must be a list of at most 100 rows")
        if not v2 and ScoringPolicyEnrollment.objects.filter(competition=competition).exists():
            raise ProtocolError("legacy scoring is disabled for this competition", code="legacy_disabled")
    except ProtocolError as exc:
        return error_response(exc)
    accepted, outcomes, rejected = 0, [], []
    for index, row in enumerate(rows):
        try:
            result = publish(competition, row) if v2 else _legacy(competition, row)
        except (ProtocolError, ValueError, TypeError) as exc:
            result = {"status": "rejected", "error": str(exc)[:300],
                      "code": getattr(exc, "code", "invalid"),
                      "retryable": getattr(exc, "retryable", False)}
            if isinstance(row, dict):
                key, revision = row.get("key"), row.get("revision")
                if isinstance(key, str) and len(key) <= 120:
                    result["key"] = key
                if type(revision) is int and 1 <= revision <= 9223372036854775807:
                    result["revision"] = revision
            if result["code"] in ("pending", "override_hold"):
                result["status"] = result["code"]
            rejected.append({"index": index, **result})
        except Exception:
            logger.exception("Scoring row %s failed", index)
            result = {"status": "rejected", "code": "server_error",
                      "error": "score could not be persisted", "retryable": True}
            rejected.append({"index": index, **result})
        else:
            accepted += 1
        outcomes.append({"index": index, **result})
    response = {"accepted": accepted, "rejected": rejected}
    if v2:
        response.update(schema_version=SCHEMA_VERSION, outcomes=outcomes)
    return JsonResponse(response, status=202 if rejected else 200)


@csrf_exempt
@never_cache
@require_POST
def upload_evidence(request):
    if not authenticated(request):
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        body = read_json(request)
        competition = _competition(body, required=True)
        team = resolve_team(competition, body, canonical=True)
        if team is None or team.status != Team.Status.APPROVED:
            raise ProtocolError("approved team not found")
        evidence = scoring_evidence.archive(competition, team, body)
    except ProtocolError as exc:
        return error_response(exc)
    except (ValueError, TypeError) as exc:
        return error_response(ProtocolError(str(exc)))
    except Exception:
        logger.exception("Evidence could not be archived")
        return JsonResponse({"error": "evidence could not be archived", "retryable": True}, status=503)
    return JsonResponse({"schema_version": "scoring-evidence/1.0", "id": str(evidence.pk),
                         "sha256": evidence.sha256, "input_sha256": evidence.input_sha256}, status=201)


def _store_readiness(competition, team, criterion, version, start, end, status, reason):
    previous = ScoringReadiness.objects.filter(competition=competition, team=team, criterion=criterion).first()
    if previous and previous.window_end > end:
        return previous
    state, _ = ScoringReadiness.objects.update_or_create(
        competition=competition, team=team, criterion=criterion,
        defaults={"policy_version": version, "window_start": start, "window_end": end,
                  "status": status, "reason_code": reason})
    return state


@csrf_exempt
@never_cache
@require_POST
@transaction.atomic
def update_readiness(request):
    if not authenticated(request):
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        body = read_json(request)
        if set(body) != {"competition", "team", "criterion", "policy_version", "window", "status", "reason_code"}:
            raise ProtocolError("invalid readiness fields")
        competition = _competition(body, required=True)
        team = resolve_team(competition, body, canonical=True)
        if team is None or team.status != Team.Status.APPROVED:
            raise ProtocolError("approved team not found")
        Team.objects.select_for_update().get(pk=team.pk)
        enrollment = enrollment_for(competition)
        if body["policy_version"] != enrollment.policy_version:
            raise ProtocolError("policy version mismatch")
        if body["criterion"] not in {key for key, _, _, _ in grading.rows_for("main", team)}:
            raise ProtocolError("criterion does not belong to this team")
        if body["status"] not in ScoringReadiness.Status.values:
            raise ProtocolError("unknown readiness status")
        if not isinstance(body["reason_code"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", body["reason_code"]):
            raise ProtocolError("reason_code must be a bounded machine code")
        window = body["window"]
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise ProtocolError("window requires start and end")
        start, end = timestamp(window["start"], "start"), timestamp(window["end"], "end")
        if start != enrollment.window_start or end < start or end > min(enrollment.window_end, timezone.now()):
            raise ProtocolError("readiness window must fit the enrolled measurement window")
        if body["status"] == "published" and not scoring_service.visible_events(
            competition, team=team).filter(stage="main", criterion=body["criterion"],
                                           window_end=end, policy_version=enrollment.policy_version).exists():
            raise ProtocolError("no accepted revision exists for this published status")
        state = _store_readiness(competition, team, body["criterion"], body["policy_version"],
                                 start, end, body["status"], body["reason_code"])
    except ProtocolError as exc:
        return error_response(exc)
    except (ValueError, TypeError) as exc:
        return error_response(ProtocolError(str(exc)))
    return JsonResponse({"status": state.status, "criterion": state.criterion,
                         "window": {"start": state.window_start.isoformat(), "end": state.window_end.isoformat()}})
