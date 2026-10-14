"""Staff-issued ground truth, scoped producer credentials and immutable receipts."""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from competitions.models import (
    ScenarioExecution, ScenarioReceipt, ScenarioVersion, ScoringExclusion, ScoringProducer,
)
from cyberrange.models import Finding, RangeApplication, ScopeAssignment, ScoringIdentityState, TargetIdentityInterval
from services.scoring_identity import (
    DIGEST, FRESHNESS, IdentityError, _iso, _lock_state, competition_window,
    require_time, resolve_team,
)

TOKEN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
FAMILIES = {"injection", "evasion", "budget_exhaustion", "baseline_poisoning", "application", "legitimate"}
OUTCOME_BOOLS = {
    "success", "objective_reached", "legitimate_ok", "protected", "response_authorized",
    "mitigated", "recovered", "action_verified", "induced", "harmful",
    "paired_build_verified", "no_valid_finding", "response_no_action_required",
}
RUNTIME_KEYS = {"deterministic", "model", "combined", "fallback", "scope", "budget", "reversal", "kill_switch"}
DECISION_BOOLS = {"context_valid", "evidence_valid", "consistency_valid"}
ASSERTION_KEYS = OUTCOME_BOOLS | DECISION_BOOLS | {f"runtime_checks.{key}" for key in RUNTIME_KEYS}
RECEIPT_ROLES = {
    "delivered": {"runner"}, "outcome": {"verifier"},
    "detection": {"competitor"}, "decision": {"competitor", "verifier"},
    "action": {"gateway"}, "probe": {"verifier"},
    "source_health": {"runner", "verifier", "gateway", "competitor", "deployment"},
    "target_verification": {"deployment"},
}


class ScenarioError(ValueError):
    pass


class ReceiptConflict(ScenarioError):
    pass


def _staff(user):
    if user is None or not user.is_active or not user.is_staff:
        raise ScenarioError("Only an active organiser may issue scoring authority.")


def _timestamp(value, name):
    if not isinstance(value, str) or len(value) > 40:
        raise ScenarioError(f"{name} must be an ISO UTC timestamp.")
    try:
        stamp = parse_datetime(value)
        return require_time(stamp, name)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScenarioError(f"{name} must be an ISO timestamp with a timezone.") from exc


def _hash(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    except (ValueError, TypeError) as exc:
        raise ScenarioError("Receipt contains an unsupported value.") from exc


def _identifier(value, name):
    if not isinstance(value, str) or not TOKEN_ID.fullmatch(value):
        raise ScenarioError(f"{name} must be a bounded opaque identifier.")


@transaction.atomic
def freeze_scenario(scenario, *, version, label, family, assertions, issued_by):
    _staff(issued_by)
    if type(version) is not int or version < 1 or label not in {"attack", "legitimate"} or family not in FAMILIES:
        raise ScenarioError("Scenario version, label or family is invalid.")
    if not isinstance(assertions, list) or not 1 <= len(assertions) <= 64:
        raise ScenarioError("A scenario requires bounded machine-checkable assertions.")
    seen = set()
    for assertion in assertions:
        if (not isinstance(assertion, dict) or set(assertion) != {"key", "operator", "expected"}
                or not isinstance(assertion["key"], str)
                or assertion["key"] not in ASSERTION_KEYS or assertion["operator"] != "eq"
                or type(assertion["expected"]) is not bool or assertion["key"] in seen):
            raise ScenarioError("Each assertion names one supported boolean check and its expected result.")
        seen.add(assertion["key"])
    definition = dict(code=scenario.code, version=version, label=label, family=family, assertions=assertions)
    return ScenarioVersion.objects.create(
        scenario=scenario, scenario_code=scenario.code, track=scenario.track,
        version=version, label=label, family=family, assertions=assertions,
        definition_sha256=_hash(definition), issued_by=issued_by,
    )


@transaction.atomic
def register_producer(competition, *, kind, valid_until, registered_by, team=None, token=None, now=None):
    _staff(registered_by)
    stamp, valid_until = require_time(now or timezone.now()), require_time(valid_until)
    _lock_state(competition)
    if kind not in {choice[0] for choice in ScoringProducer._meta.get_field("kind").choices}:
        raise ScenarioError("Unknown producer kind.")
    if kind == "competitor" and team is None:
        raise ScenarioError("A competitor producer must name its canonical team.")
    if team is not None and resolve_team(competition, team.team_identifier).pk != team.pk:
        raise ScenarioError("Producer team belongs to another competition.")
    _, end = competition_window(competition)
    if not stamp < valid_until <= end + dt.timedelta(days=7):
        raise ScenarioError("Producer validity must be bounded by the evidence ingestion period.")
    token = token or secrets.token_urlsafe(32)
    if not isinstance(token, str) or len(token) < 32 or len(token) > 256:
        raise ScenarioError("Producer credentials must contain at least 32 characters.")
    producer = ScoringProducer.objects.create(
        competition=competition, kind=kind, team=team,
        credential_sha256=hashlib.sha256(token.encode()).hexdigest(),
        valid_from=stamp, valid_until=valid_until, registered_by=registered_by,
    )
    return producer, token


def authenticate_producer(producer_id, token, *, now=None):
    stamp = require_time(now or timezone.now())
    try:
        producer_id = uuid.UUID(str(producer_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ScenarioError("Invalid producer credential.") from exc
    producer = ScoringProducer.objects.filter(producer_id=producer_id).select_related("competition", "team").first()
    if (producer is None or not isinstance(token, str) or not 32 <= len(token) <= 256
            or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), producer.credential_sha256)
            or producer.revoked_at is not None or not producer.valid_from <= stamp < producer.valid_until):
        raise ScenarioError("Invalid producer credential.")
    return producer


@transaction.atomic
def issue_case(competition, *, objective, scenario_version, target_id, starts_at, deadline_at,
               opportunity_key, issued_by, actor_team=None, environment="competition", now=None,
               required_producer_ids=None, defender_team=None):
    _staff(issued_by)
    stamp = require_time(now or timezone.now())
    starts_at, deadline_at = require_time(starts_at), require_time(deadline_at)
    _identifier(opportunity_key, "opportunity_key")
    state = _lock_state(competition)
    from services.scoring_identity import _check_source
    _check_source(state)
    if not dt.timedelta(0) <= stamp - state.refreshed_at < FRESHNESS:
        raise ScenarioError("Identity must be refreshed before issuing a case.")
    window_start, window_end = competition_window(competition)
    if (not window_start <= starts_at < deadline_at <= window_end or starts_at < stamp
            or environment not in {"competition", "rehearsal"}):
        raise ScenarioError("A case must be issued before its bounded execution window.")
    matches = list(TargetIdentityInterval.objects.filter(
        competition=competition, target_id=target_id, valid_from__lte=starts_at,
        valid_until__gt=starts_at,
    ).select_related("team", "range_target")[:2])
    if len(matches) != 1:
        raise ScenarioError("The case target has no unique fresh artifact and owner.")
    target = matches[0]
    if defender_team is None:
        if target.team.track != scenario_version.track:
            raise ScenarioError("This scenario requires an explicit canonical defender team.")
        defender_team = target.team
    resolve_team(competition, defender_team.team_identifier)
    if defender_team.team_type != "blue" or defender_team.status != "approved" or defender_team.track != scenario_version.track:
        raise ScenarioError("The defender role does not match the scenario track.")
    if defender_team.pk != target.team_id and not ScopeAssignment.objects.filter(
        competition=competition, team=defender_team, application=target.range_target,
        action_scope__isnull=False, active=True, valid_from__lte=starts_at,
    ).filter(Q(valid_until=None) | Q(valid_until__gt=starts_at)).exists():
        raise ScenarioError("The defender has no fresh explicit authority over this workload.")
    if objective.team.competition_id != competition.pk or objective.team_id not in {target.team_id, defender_team.pk, getattr(actor_team, "pk", None)}:
        raise ScenarioError("Objective does not belong to the target or assigned actor.")
    if target.application.startswith("APP") and ScenarioExecution.objects.filter(
        competition=competition, scenario_version=scenario_version, opportunity_key=opportunity_key,
        objective_code=objective.code, target_identity__application=target.application,
        target_identity__enterprise=target.enterprise,
    ).exists():
        raise ScenarioError("This paired-build opportunity already has a case for this enterprise.")
    producers = ScoringProducer.objects.filter(
        competition=competition, valid_from__lte=starts_at, valid_until__gte=deadline_at, revoked_at=None,
    ).filter(Q(team=None) | Q(team=defender_team))
    if required_producer_ids is not None:
        if not isinstance(required_producer_ids, list) or not 1 <= len(required_producer_ids) <= 16:
            raise ScenarioError("Required producer IDs must be a bounded nonempty list.")
        try:
            selected = {uuid.UUID(str(value)) for value in required_producer_ids}
        except (ValueError, AttributeError) as exc:
            raise ScenarioError("Required producer IDs must be UUIDs.") from exc
        producers = producers.filter(producer_id__in=selected)
        if producers.count() != len(selected):
            raise ScenarioError("A required producer has wrong scope or validity.")
    by_role = {role: [] for role in ("runner", "verifier", "competitor", "gateway")}
    for producer in producers:
        if producer.kind in by_role:
            by_role[producer.kind].append(str(producer.producer_id))
    needed = {"runner", "verifier", "competitor"}
    if any(a["key"] in {"action_verified", "induced", "harmful"} or a["key"].startswith("runtime_checks.")
           for a in scenario_version.assertions):
        needed.add("gateway")
    if any(not by_role[role] for role in needed):
        raise ScenarioError("Register the required runner, verifier, defender and action producers before issuing this case.")
    if required_producer_ids is None and any(len(ids) > 1 for ids in by_role.values()):
        raise ScenarioError("Producer selection is ambiguous; explicitly name the required producer IDs.")
    scope = None
    if actor_team is not None:
        resolve_team(competition, actor_team.team_identifier)
        if actor_team.team_type != "red":
            raise ScenarioError("An attack actor must be the assigned Red team.")
        scopes = list(ScopeAssignment.objects.filter(
            competition=competition, team=actor_team, target_id=target_id,
            active=True, valid_from__lte=starts_at,
        ).filter(Q(valid_until=None) | Q(valid_until__gt=starts_at)).order_by("pk"))
        if not scopes or any(s.target_team_identifier != target.team_identifier
                             or s.defender_team_identifier != defender_team.team_identifier for s in scopes):
            raise ScenarioError("The actor has no active canonical assignment to this target.")
        scope = scopes[0]
    return ScenarioExecution.objects.create(
        competition=competition, objective=objective, objective_code=objective.code,
        objective_team_identifier=objective.team.team_identifier, scenario_version=scenario_version,
        opportunity_key=opportunity_key, actor_team=actor_team, target_team=target.team, defender_team=defender_team,
        target_identity=target, scope_identity=scope, assignment_revision=state.revision,
        starts_at=starts_at, deadline_at=deadline_at, environment=environment, issued_by=issued_by,
        required_producers={role: sorted(ids) for role, ids in by_role.items()},
    )


def _evidence_refs(values):
    if not isinstance(values, list) or len(values) > 128:
        raise ScenarioError("Evidence references must be a bounded list.")
    seen = set()
    for ref in values:
        if not isinstance(ref, dict) or set(ref) != {"family", "id"}:
            raise ScenarioError("Evidence references require family and id.")
        family, identifier = ref["family"], ref["id"]
        if not isinstance(family, str) or family not in {"application", "infrastructure", "metrics", "scenario", "ai"}:
            raise ScenarioError("Unknown evidence family.")
        _identifier(identifier, "evidence id")
        if family in {"application", "infrastructure"}:
            prefix = "evt_" if family == "application" else "infra_"
            if not re.fullmatch(prefix + r"[0-9a-f]{64}", identifier):
                raise ScenarioError("Evidence ID does not belong to its declared family.")
        pair = (family, identifier)
        if pair in seen:
            raise ScenarioError("Evidence references must be distinct.")
        seen.add(pair)


def _measurements(kind, producer, values, case):
    if not isinstance(values, dict):
        raise ScenarioError("Measurements must be an object.")
    allowed = {
        "delivered": {"delivered"},
        "outcome": OUTCOME_BOOLS | {"response_started_at", "incident_id", "action_id", "runtime_checks"},
        "detection": {"classification", "incident_id"},
        "decision": DECISION_BOOLS if producer.kind == "verifier" else {"decision_id", "action_ids", "correlation_ids", "rationale_reference"},
        "action": {"action_id", "state", "action_type"},
        "probe": {"successful", "expected", "completed", "target_digest"},
        "source_health": {"start", "end", "expected", "completed", "lost"},
        "target_verification": {"target_digest"},
    }[kind]
    if not values or not set(values).issubset(allowed):
        raise ScenarioError("Receipt contains unsupported measurement fields.")
    for key, value in values.items():
        if key in OUTCOME_BOOLS | DECISION_BOOLS | {"delivered", "successful"} and type(value) is not bool:
            raise ScenarioError(f"{key} must be a boolean.")
        if key in {"incident_id", "decision_id", "rationale_reference", "action_id"}:
            _identifier(value, key)
    if kind == "delivered" and set(values) != {"delivered"}:
        raise ScenarioError("A delivery receipt must record delivered.")
    if kind == "detection" and (set(values) != allowed or not isinstance(values["classification"], str)
                                or values["classification"] not in {"attack", "legitimate"}):
        raise ScenarioError("A detection requires classification and incident_id.")
    if kind == "outcome" and any(values.get(key) for key in ("action_verified", "induced", "harmful")):
        if not values.get("action_id"):
            raise ScenarioError("A verified action consequence must name its gateway action_id.")
        if not case.required_producers.get("gateway"):
            raise ScenarioError("The issued case has no trusted action gateway.")
    if "runtime_checks" in values:
        checks = values["runtime_checks"]
        if not isinstance(checks, dict) or not set(checks).issubset(RUNTIME_KEYS) or any(type(v) is not bool for v in checks.values()):
            raise ScenarioError("Runtime checks require supported boolean assertions.")
    if "response_started_at" in values and values["response_started_at"] is not None:
        response_time = _timestamp(values["response_started_at"], "response_started_at")
        if case is None or not case.starts_at <= response_time < case.deadline_at:
            raise ScenarioError("Response start is outside the issued case.")
    if values.get("response_no_action_required"):
        checks = {a["key"]: a["expected"] for a in case.scenario_version.assertions}
        if checks.get("response_no_action_required") is not True:
            raise ScenarioError("Correct inaction was not declared by the frozen scenario.")
    for key in ("action_ids", "correlation_ids"):
        if key in values:
            items = values[key]
            if not isinstance(items, list) or len(items) > 32:
                raise ScenarioError(f"{key} must be a bounded list.")
            for value in items:
                _identifier(value, key)
            if len(items) != len(set(items)):
                raise ScenarioError(f"{key} must be distinct.")
    if kind == "action":
        if (set(values) != allowed or not isinstance(values.get("state"), str)
                or not isinstance(values.get("action_type"), str)
                or values.get("state") not in {"requested", "authorized", "executed", "failed", "blocked", "verified", "reverted"}
                or values.get("action_type") not in {"alert", "block-ip", "snapshot-evidence", "revert"}):
            raise ScenarioError("Action state or capability is invalid.")
    if kind == "probe":
        if set(values) != allowed or type(values.get("expected")) is not int or values["expected"] != 1 or type(values.get("completed")) is not int or values["completed"] != 1:
            raise ScenarioError("A probe records exactly one completed functional check.")
    if "target_digest" in values and (not isinstance(values["target_digest"], str) or not DIGEST.fullmatch(values["target_digest"])):
        raise ScenarioError("Target digest is invalid.")
    if kind == "target_verification" and set(values) != allowed:
        raise ScenarioError("A target verification requires its observed digest.")
    if kind == "source_health":
        if set(values) != allowed or any(type(values[k]) is not int or not 0 <= values[k] <= 1000000 for k in ("expected", "completed", "lost")):
            raise ScenarioError("Health counts must be bounded nonnegative integers.")
        if values["completed"] + values["lost"] > values["expected"]:
            raise ScenarioError("Health counts exceed expected observations.")
        start, end = _timestamp(values["start"], "start"), _timestamp(values["end"], "end")
        if not dt.timedelta(0) < end - start <= dt.timedelta(hours=1):
            raise ScenarioError("Health receipts cover a positive interval of at most one hour.")


@transaction.atomic
def record_receipt(producer, payload, *, now=None):
    stamp = require_time(now or timezone.now())
    # Serialize insert and cursor reads per competition. PostgreSQL sequence
    # allocation alone would let a late commit disappear behind a cursor.
    _lock_state(producer.competition)
    producer.refresh_from_db()
    if producer.revoked_at is not None or not producer.valid_from <= stamp < producer.valid_until:
        raise ScenarioError("Producer credential is expired or revoked.")
    required = {"producer_id", "receipt_id", "kind", "observed_at", "clock_uncertainty_ms", "measurements", "evidence_refs"}
    if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - required - {"case_id", "target_id"}:
        raise ScenarioError("Receipt shape is invalid.")
    if str(payload["producer_id"]) != str(producer.producer_id):
        raise ScenarioError("Receipt producer does not match its credential.")
    try:
        receipt_id = uuid.UUID(payload["receipt_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ScenarioError("receipt_id must be a UUID.") from exc
    payload_sha256 = _hash(payload)
    existing = ScenarioReceipt.objects.filter(producer=producer, receipt_id=receipt_id).first()
    if existing:
        if not hmac.compare_digest(existing.payload_sha256, payload_sha256):
            raise ReceiptConflict("A receipt ID cannot be reused with changed content.")
        return existing, False
    kind = payload["kind"]
    if not isinstance(kind, str) or kind not in RECEIPT_ROLES or producer.kind not in RECEIPT_ROLES[kind]:
        raise ScenarioError("Producer is not authorized to emit this receipt kind.")
    observed = _timestamp(payload["observed_at"], "observed_at")
    if not producer.valid_from <= observed < producer.valid_until or observed > stamp + dt.timedelta(seconds=2):
        raise ScenarioError("Observed time is outside the producer validity window.")
    uncertainty = payload["clock_uncertainty_ms"]
    if type(uncertainty) is not int or not 0 <= uncertainty <= 3600000:
        raise ScenarioError("Clock uncertainty must be explicit bounded milliseconds.")
    case = None
    if payload.get("case_id") is not None:
        try:
            case_id = uuid.UUID(payload["case_id"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ScenarioError("case_id must be a UUID.") from exc
        case = ScenarioExecution.objects.filter(competition=producer.competition, case_id=case_id).select_related(
            "scenario_version__scenario", "target_team", "defender_team", "actor_team", "objective", "target_identity",
        ).first()
        if case is None or not case.starts_at <= observed < case.deadline_at:
            raise ScenarioError("Receipt is outside its issued case and execution window.")
        if case.actor_team_id is not None and not ScopeAssignment.objects.filter(
            pk=case.scope_identity_id, competition=producer.competition,
            team_id=case.actor_team_id, target_id=case.target_identity.target_id,
            target_team_identifier=case.target_identity.team_identifier,
            defender_team_identifier=case.defender_team.team_identifier,
            active=True, valid_from__lte=observed,
        ).filter(Q(valid_until=None) | Q(valid_until__gt=observed)).exists():
            raise ScenarioError("The case actor's issued scope was no longer valid at observation time.")
        if case.defender_team_id != case.target_team_id and not ScopeAssignment.objects.filter(
            competition=producer.competition, team_id=case.defender_team_id,
            target_id=case.target_identity.target_id, target_team_identifier=case.target_identity.team_identifier,
            action_scope__isnull=False, active=True, valid_from__lte=observed,
        ).filter(Q(valid_until=None) | Q(valid_until__gt=observed)).exists():
            raise ScenarioError("The defender's managed workload authority ended before this observation.")
    if case is None and kind not in {"probe", "source_health", "target_verification"}:
        raise ScenarioError("This receipt requires a platform-issued case.")
    if case is not None and str(producer.producer_id) not in case.required_producers.get(producer.kind, []):
        raise ScenarioError("Producer is not part of the frozen case authority.")
    if producer.kind == "competitor" and case is not None and producer.team_id != case.defender_team_id:
        raise ScenarioError("A competitor may report only its own defensive detections or decisions.")
    _evidence_refs(payload["evidence_refs"])
    _measurements(kind, producer, payload["measurements"], case)
    target_id = payload.get("target_id") or (case.target_identity.target_id if case else None)
    target = None
    if kind == "target_verification":
        from services.scoring_identity import refresh_identity, validate_target_source
        declared = RangeApplication.objects.filter(
            competition=producer.competition, application_instance_id=target_id,
        ).select_related("team", "competition").first()
        if (declared is None or declared.team.track != "ai"
                or (producer.team_id is not None and producer.team_id != declared.team_id)
                or declared.image_digest != payload["measurements"]["target_digest"]):
            raise ScenarioError("Artifact receipt does not match an issued AI target and its frozen digest.")
        try:
            validate_target_source(declared)
        except (IdentityError, ValidationError) as exc:
            raise ScenarioError("The declared target is not canonical.") from exc
        if observed < stamp - FRESHNESS or observed > stamp:
            raise ScenarioError("Artifact confirmation must be fresh and cannot be future-dated.")
        if declared.verified_at is not None and observed < declared.verified_at:
            raise ScenarioError("A deployment confirmation cannot move backwards.")
        # Authentication is the trust boundary. Teams cannot reach this
        # write path with a competitor token or choose the frozen digest.
        RangeApplication.objects.filter(pk=declared.pk).update(verified_at=observed)
        refresh_identity(producer.competition, now=stamp)
    if kind != "source_health" or target_id is not None:
        identity_time = stamp if kind == "target_verification" else observed
        matches = list(TargetIdentityInterval.objects.filter(
            competition=producer.competition, target_id=target_id, valid_from__lte=identity_time,
            valid_until__gt=identity_time,
        ).select_related("range_target", "team")[:2])
        if len(matches) != 1:
            raise ScenarioError("Receipt target has no unique eligible event-time identity.")
        target = matches[0]
        if case is not None and (target.team_id != case.target_team_id or target.target_id != case.target_identity.target_id
                                 or target.frozen_digest != case.target_identity.frozen_digest):
            raise ScenarioError("The issued case target or frozen artifact changed.")
        allowed_team_id = case.defender_team_id if case else target.team_id
        if producer.team_id and producer.team_id != allowed_team_id:
            managed = kind == "source_health" and ScopeAssignment.objects.filter(
                competition=producer.competition, team_id=producer.team_id, target_id=target.target_id,
                active=True, action_scope__isnull=False, valid_from__lte=observed,
            ).filter(Q(valid_until=None) | Q(valid_until__gt=observed)).exists()
            if not managed:
                raise ScenarioError("Producer is scoped to another target team.")
        if kind in {"probe", "target_verification"} and payload["measurements"]["target_digest"] != target.frozen_digest:
            raise ScenarioError("Observed target digest does not match the frozen artifact.")
    if kind == "source_health":
        values = payload["measurements"]
        start, end = _timestamp(values["start"], "start"), _timestamp(values["end"], "end")
        if start < producer.valid_from or end > observed + dt.timedelta(seconds=2):
            raise ScenarioError("Health interval is outside its producer observation time.")
        if target is not None:
            from services.scoring_identity import _cover
            intervals = list(TargetIdentityInterval.objects.filter(
                competition=producer.competition, target_id=target.target_id, valid_from__lt=end, valid_until__gt=start))
            if any(row.team_identifier != target.team_identifier for row in intervals):
                raise ScenarioError("Health interval crosses a workload ownership change.")
            _cover(intervals, start, end)
        prior_health = ScenarioReceipt.objects.filter(
            producer=producer, kind="source_health", observed_at__gte=start - dt.timedelta(hours=1))
        prior_health = prior_health.filter(target_identity__target_id=target_id) if target_id else prior_health.filter(target_identity=None)
        for earlier in prior_health:
            prior = earlier.measurements
            if _timestamp(prior["start"], "start") < end and _timestamp(prior["end"], "end") > start:
                raise ReceiptConflict("Health intervals cannot overlap for one producer.")
    if payload["measurements"].get("no_valid_finding") and case is not None:
        if Finding.objects.filter(competition=producer.competition, target_team=case.target_team,
                                  scoring_objective=case.objective, status="valid").exists():
            raise ScenarioError("A reviewed valid finding contradicts the protection claim.")
    if payload["measurements"].get("paired_build_verified") and not _paired_build_verified(case):
        raise ScenarioError("Both issued builds need trusted execution receipts before claiming a paired comparison.")
    receipt = ScenarioReceipt.objects.create(
        receipt_id=receipt_id, producer=producer, competition=producer.competition,
        execution=case, target_identity=target, kind=kind, producer_kind=producer.kind,
        team_identifier=producer.team.team_identifier if producer.team_id else (
            case.defender_team.team_identifier if case else (target.team_identifier if target else "")),
        observed_at=observed, received_at=stamp, clock_uncertainty_ms=uncertainty,
        measurements=payload["measurements"], evidence_refs=payload["evidence_refs"], payload_sha256=payload_sha256,
    )
    return receipt, True


@transaction.atomic
def write_finding_source(finding, write):
    previous = Finding.objects.filter(pk=finding.pk).first() if finding.pk else None
    state = ScoringIdentityState.objects.select_for_update().filter(competition_id=finding.competition_id).first()
    result = write()
    fields = ("status", "severity", "scoring_objective_id", "target_team_id", "telemetry_event_ids", "correlation_ids")
    if state is None or (previous is not None and all(getattr(previous, field) == getattr(finding, field) for field in fields)):
        return result
    conditions = Q(pk__in=[])
    for version in (previous, finding):
        if version is not None and version.scoring_objective_id:
            conditions |= Q(objective_id=version.scoring_objective_id, target_team_id=version.target_team_id)
    for case in ScenarioExecution.objects.filter(competition_id=finding.competition_id).filter(conditions).select_related("target_identity"):
        values = {"finding_id": str(finding.pk), "status": finding.status,
                  "no_valid_finding": not Finding.objects.filter(
                      competition_id=finding.competition_id, target_team_id=case.target_team_id,
                      scoring_objective_id=case.objective_id, status="valid",
                  ).exists()}
        ScenarioReceipt.objects.create(
            receipt_id=uuid.uuid4(), producer=None, competition_id=finding.competition_id,
            execution=case, target_identity=case.target_identity, kind="finding_review",
            producer_kind="platform", team_identifier=case.target_identity.team_identifier,
            observed_at=timezone.now(), received_at=timezone.now(), clock_uncertainty_ms=0,
            measurements=values, evidence_refs=[], payload_sha256=_hash(values),
        )
    return result


@transaction.atomic
def approve_exclusion(competition, *, start, end, incident_reference, approved_by):
    _staff(approved_by)
    _lock_state(competition)
    start, end = require_time(start), require_time(end)
    window_start, window_end = competition_window(competition)
    _identifier(incident_reference, "incident_reference")
    if not window_start <= start < end <= window_end:
        raise ScenarioError("Exclusion must lie within the scoring window.")
    return ScoringExclusion.objects.create(competition=competition, start=start, end=end,
        reason="shared_infrastructure", incident_reference=incident_reference, approved_by=approved_by)


def _case_dict(case):
    version = case.scenario_version
    no_valid_finding = not Finding.objects.filter(
        competition=case.competition, target_team=case.target_team,
        scoring_objective=case.objective, status="valid",
    ).exists()
    return dict(sequence=case.pk, case_id=str(case.case_id), opportunity_key=case.opportunity_key, scenario={
        "code": version.scenario_code, "version": version.version, "label": version.label,
        "family": version.family, "assertions": version.assertions, "sha256": version.definition_sha256,
    }, objective={"id": case.objective_id, "code": case.objective_code, "team": case.objective_team_identifier},
        actor_team=case.actor_team.team_identifier if case.actor_team_id else None,
        target_team=case.target_team.team_identifier, defender_team=case.defender_team.team_identifier,
        target_id=case.target_identity.target_id,
        frozen_digest=case.target_identity.frozen_digest, assignment_revision=case.assignment_revision,
        starts_at=_iso(case.starts_at), deadline_at=_iso(case.deadline_at), environment=case.environment,
        required_producers=case.required_producers,
        no_valid_finding=no_valid_finding, paired_build_verified=_paired_build_verified(case))


def _paired_build_verified(case):
    if case is None or case.target_identity.application[:3] != "APP":
        return False
    peers = _paired_cases(case)
    observed = set()
    for peer in peers:
        receipts = list(peer.receipts.filter(kind__in=["delivered", "outcome"]))
        delivered = any(r.producer_kind == "runner" and r.kind == "delivered"
                        and r.measurements.get("delivered") is True and r.clock_uncertainty_ms <= 2000 for r in receipts)
        checked = any(r.producer_kind == "verifier" and r.kind == "outcome"
                      and type(r.measurements.get("objective_reached")) is bool
                      and type(r.measurements.get("legitimate_ok")) is bool
                      and r.clock_uncertainty_ms <= 2000 for r in receipts)
        if delivered and checked:
            observed.add(peer.target_identity.enterprise)
    return observed == {"ENT-A", "ENT-B"}


def _paired_cases(case):
    if not case.target_identity.application.startswith("APP"):
        return ScenarioExecution.objects.none()
    return ScenarioExecution.objects.filter(
        competition=case.competition, scenario_version=case.scenario_version,
        opportunity_key=case.opportunity_key, objective_code=case.objective_code,
        environment=case.environment, target_identity__application=case.target_identity.application,
    ).select_related("scenario_version__scenario", "target_identity", "target_team", "defender_team", "actor_team", "objective__team")


def _receipt_dict(receipt):
    result = dict(sequence=receipt.pk, receipt_id=str(receipt.receipt_id),
        case_id=str(receipt.execution.case_id) if receipt.execution_id else None,
        kind=receipt.kind, producer_id=str(receipt.producer.producer_id) if receipt.producer_id else None,
        producer_kind=receipt.producer_kind,
        team=receipt.team_identifier, observed_at=_iso(receipt.observed_at), received_at=_iso(receipt.received_at),
        clock_uncertainty_ms=receipt.clock_uncertainty_ms, measurements=receipt.measurements,
        evidence_refs=receipt.evidence_refs, sha256=receipt.payload_sha256)
    if receipt.target_identity_id:
        result.update(target_id=receipt.target_identity.target_id, target_team=receipt.target_identity.team_identifier,
                      assignment_revision=receipt.target_identity.revision)
    return result


def _cases(queryset):
    return queryset.select_related("scenario_version__scenario", "target_identity", "target_team", "defender_team", "actor_team", "objective__team")


def _receipts(queryset):
    return queryset.select_related("producer", "execution", "target_identity")


@transaction.atomic
def receipt_snapshot(competition, *, after=0, cases_after=0, limit=1000):
    _lock_state(competition)
    if any(type(v) is not int or not 0 <= v <= 2**63 - 1 for v in (after, cases_after)) or type(limit) is not int or not 1 <= limit <= 1000:
        raise ScenarioError("Cursor and limit are invalid.")
    receipts = list(_receipts(ScenarioReceipt.objects.filter(competition=competition, pk__gt=after).order_by("pk"))[:limit + 1])
    pending_cases = list(_cases(ScenarioExecution.objects.filter(competition=competition, pk__gt=cases_after).order_by("pk"))[:limit + 1])
    case_ids = {r.execution_id for r in receipts[:limit] if r.execution_id}
    referenced = _cases(ScenarioExecution.objects.filter(pk__in=case_ids))
    cases = {c.pk: c for c in referenced}
    case_budget = max(0, limit - len(cases))
    cases.update({c.pk: c for c in pending_cases[:case_budget]})
    # Completing the second build changes the paired comparison for both.
    peers = {}
    for case in cases.values():
        peers.update({c.pk: c for c in _paired_cases(case)})
    cases.update(peers)
    if len(cases) > 2000:
        raise ScenarioError("Export is too large; reduce the limit.")
    return dict(schema_version="scoring-receipts/1.0", competition=competition.pk, captured_at=_iso(timezone.now()),
        receipts=[_receipt_dict(r) for r in receipts[:limit]], cases=[_case_dict(c) for c in cases.values()],
        next_cursor=receipts[min(len(receipts), limit) - 1].pk if receipts else after,
        has_more=len(receipts) > limit,
        cases_next_cursor=pending_cases[min(len(pending_cases), case_budget) - 1].pk if pending_cases and case_budget else cases_after,
        cases_has_more=len(pending_cases) > case_budget)


@transaction.atomic
def scenario_snapshot(competition, *, start, end):
    _lock_state(competition)
    start, end = require_time(start), require_time(end)
    if not dt.timedelta(0) < end - start <= dt.timedelta(minutes=15):
        raise ScenarioError("Request a positive window of at most 15 minutes.")
    cases = list(_cases(ScenarioExecution.objects.filter(competition=competition, starts_at__lt=end, deadline_at__gt=start))[:1001])
    receipts = list(_receipts(ScenarioReceipt.objects.filter(competition=competition, observed_at__gte=start, observed_at__lt=end))[:5001])
    # Health windows are exported whole, even when only part intersects.
    health = []
    for receipt in _receipts(ScenarioReceipt.objects.filter(competition=competition, kind="source_health", observed_at__gte=start, observed_at__lt=end + dt.timedelta(hours=1))):
        values = receipt.measurements
        if _timestamp(values["start"], "start") < end and _timestamp(values["end"], "end") > start:
            row = _receipt_dict(receipt)
            row.update(values)
            health.append(row)
    if len(cases) > 1000 or len(receipts) > 5000 or len(health) > 5000:
        raise ScenarioError("Export is too large; request a narrower window.")
    case_rows = []
    for case in cases:
        row = _case_dict(case)
        row["receipts"] = [_receipt_dict(r) for r in receipts if r.execution_id == case.pk]
        case_rows.append(row)
    exclusions = ScoringExclusion.objects.filter(competition=competition, start__lt=end, end__gt=start)
    return dict(schema_version="scoring-scenarios/1.0", captured_at=_iso(timezone.now()), competition=competition.pk,
        window={"start": _iso(start), "end": _iso(end)}, cases=case_rows, health=health,
        probes=[_receipt_dict(r) for r in receipts if r.kind == "probe"],
        exclusions=[dict(id=r.pk, start=_iso(r.start), end=_iso(r.end), reason=r.reason) for r in exclusions])
