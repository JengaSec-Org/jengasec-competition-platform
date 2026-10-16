"""Trusted canonical attribution. Native range identifiers never name a scorer."""
import datetime as dt
import hashlib
import ipaddress
import json
import re

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.utils import timezone

from accounts.models import Team
from cyberrange.scoring_models import (
    ActionScope, IPAllocation, RangeApplication, ScopeAssignment,
    ScoringIdentityState, TargetIdentityInterval, TeamIdentityInterval,
)

FRESHNESS = dt.timedelta(seconds=120)
TEAM_ID = re.compile(r"JS[0-9]{2}-[BR]-[0-9]{3,12}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
PINNED_DIGEST = re.compile(r"[^@\s]+@(sha256:[0-9a-f]{64})\Z")
ACTIONS = frozenset({"alert", "block-ip", "snapshot-evidence", "revert"})


class IdentityError(ValueError):
    pass


@transaction.atomic
def write_identity_source(instance, write):
    """Keep ordinary model saves and their attribution interval in one commit."""
    previous = None
    if instance.pk:
        previous = instance.__class__.objects.filter(pk=instance.pk).first()
    competition_ids = {getattr(instance, "competition_id", None), getattr(previous, "competition_id", None)}
    # WireGuard profiles obtain their competition through the team.
    for row in (instance, previous):
        if row is not None and getattr(row, "team_id", None):
            competition_ids.add(row.team.competition_id)
    states = list(ScoringIdentityState.objects.select_for_update().filter(
        competition_id__in=competition_ids - {None},
    ).select_related("competition").order_by("competition_id"))
    if isinstance(instance, Team) and previous and states:
        frozen_fields = ("competition_id", "team_identifier", "team_type", "track")
        if any(getattr(instance, field) != getattr(previous, field) for field in frozen_fields):
            raise IdentityError("An enrolled team's competition, canonical identifier and scoring role are immutable.")
    result = write()
    for state in states:
        refresh_identity(state.competition)
    return result


def require_time(value, name="time"):
    if not isinstance(value, dt.datetime) or timezone.is_naive(value):
        raise IdentityError(f"{name} must be an aware timestamp.")
    return value.astimezone(dt.timezone.utc)


def resolve_team(competition, identifier):
    if not isinstance(identifier, str) or not TEAM_ID.fullmatch(identifier):
        raise IdentityError("Use the canonical team identifier.")
    matches = list(Team.objects.filter(competition=competition, team_identifier__iexact=identifier)[:2])
    if len(matches) != 1 or matches[0].team_identifier != identifier:
        raise IdentityError("Team identity is missing, ambiguous or noncanonical.")
    team = matches[0]
    if (team.team_type not in {"red", "blue"} or team.track not in {"application", "ai"}
            or identifier.split("-")[1] != {"red": "R", "blue": "B"}[team.team_type]):
        raise IdentityError("Team role does not agree with its canonical identifier.")
    return team


def competition_window(competition):
    try:
        enrollment_model = apps.get_model("scoring", "ScoringPolicyEnrollment")
    except LookupError as exc:
        raise IdentityError("Scoring policy enrollment is unavailable.") from exc
    enrollment = enrollment_model.objects.filter(competition=competition).first()
    if enrollment is None:
        raise IdentityError("Competition is not enrolled in automatic scoring.")
    return enrollment.window_start, enrollment.window_end


def _enterprise(team):
    try:
        return {"a": "ENT-A", "b": "ENT-B"}[team.enterprise]
    except KeyError as exc:
        raise IdentityError("Team has no canonical enterprise.") from exc


def validate_target_source(target):
    team = resolve_team(target.competition, target.team.team_identifier)
    if team.team_type != "blue" or team.status != Team.Status.APPROVED:
        raise ValidationError("Only an approved Blue team may own a target.")
    enterprise = _enterprise(team)
    cell_pattern = r"APP0[1-9]" if team.track == "application" else r"AI[0-9]{2}"
    if not re.fullmatch(cell_pattern, team.cell_id):
        raise ValidationError("Target team has no canonical cell.")
    expected_namespace = f"jengasec-{enterprise.lower()}-{team.cell_id.lower()}"
    if (target.competition_id != team.competition_id or target.enterprise != enterprise
            or target.application != team.cell_id
            or target.application_instance_id != f"{enterprise}/{team.cell_id}"
            or target.namespace != expected_namespace
            or team.namespace != expected_namespace):
        raise ValidationError("Target does not match the issued team, cell and namespace.")
    if "%" in target.ip_address:
        raise ValidationError("Scoped IPv6 addresses are not scoring targets.")
    try:
        address = ipaddress.ip_address(target.ip_address)
    except ValueError as exc:
        raise ValidationError("Target address is invalid.") from exc
    if address.is_multicast or address.is_unspecified or address.is_loopback:
        raise ValidationError("Target address is not an assigned workload address.")
    if team.track == "application" and (not team.service_ip or str(address) != team.service_ip):
        raise ValidationError("Application target address must match the team's reserved service IP.")
    if target.protocol not in {"tcp", "udp"} or type(target.port) is not int or not 1 <= target.port <= 65535:
        raise ValidationError("Target transport is invalid.")
    if target.image_digest and not DIGEST.fullmatch(target.image_digest):
        raise ValidationError("A frozen image must have a verified sha256 digest.")


def _lock_state(competition):
    state = ScoringIdentityState.objects.select_for_update().filter(competition=competition).first()
    if state is None:
        raise IdentityError("Identity history has not been enrolled.")
    return state


def _source_hash(competition):
    """Detect unsupported bulk writes until the source is reconciled."""
    from competitions.models import TargetAssignment
    from cyberrange.models import WireGuardProfile
    rows = {
        "teams": list(Team.objects.filter(competition=competition).order_by("pk").values(
            "pk", "team_identifier", "team_type", "track", "enterprise", "cell_id", "namespace", "service_ip", "status")),
        "targets": list(RangeApplication.objects.filter(competition=competition).order_by("pk").values()),
        "assignments": list(TargetAssignment.objects.filter(competition=competition).order_by("pk").values(
            "pk", "red_team_id", "target_team_id", "is_active")),
        "sources": list(WireGuardProfile.objects.filter(team__competition=competition).order_by("pk").values(
            "pk", "team_id", "address", "revoked", "active", "last_handshake_at", "user__profile__participant_identifier")),
        "actions": list(ActionScope.objects.filter(competition=competition).order_by("pk").values(
            "pk", "team_id", "target_id", "active", "source_cidrs", "allowed_actions",
            "approved_by__is_staff", "approved_by__is_active")),
    }
    submission_model = apps.get_model("deployments", "AppSubmission")
    wanted = {"pk", "team_id", "port", "frozen_at", "frozen_verified_digest", "resolved_digest",
              "deployed_digest_verified_at", "extra_images", "frozen_extra_images"}
    available = {f.name for f in submission_model._meta.fields} | {"pk", "team_id"}
    rows["artifacts"] = list(submission_model.objects.filter(competition=competition).order_by("pk").values(*sorted(wanted & available)))
    return hashlib.sha256(json.dumps(rows, cls=DjangoJSONEncoder, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _check_source(state):
    if state.source_sha256 != _source_hash(state.competition):
        raise IdentityError("The platform source changed without an identity refresh.")


def _advance(row, *, end, stamp, verified_at=None):
    """The only interval mutation: close, or extend continuous verified coverage."""
    end = max(row.valid_from, end)
    fields = {"valid_until": end, "updated_at": stamp}
    if verified_at is not None:
        fields["verified_at"] = verified_at
    models.QuerySet.update(row.__class__.objects.filter(pk=row.pk), **fields)


def _replace_open(model, competition, desired, stamp, revision, key):
    current = {key(row): row for row in model.objects.filter(competition=competition, valid_until=None)}
    for identity, row in current.items():
        fields = desired.get(identity)
        if fields is None or any(getattr(row, field) != value for field, value in fields.items()):
            _advance(row, end=stamp, stamp=stamp)
        else:
            desired.pop(identity)
    for fields in desired.values():
        model.objects.create(competition=competition, revision=revision, valid_from=stamp, **fields)


def _eligible_deployment(target, stamp):
    """Only owner-issued frozen content plus fresh deployed-content proof qualifies."""
    extra_digests = []
    if target.team.track == "application":
        submission_model = apps.get_model("deployments", "AppSubmission")
        required = {"resolved_digest", "deployed_digest_verified_at", "frozen_verified_digest",
                    "extra_images", "frozen_extra_images"}
        if not required.issubset({f.name for f in submission_model._meta.fields}):
            return None
        attempts = list(submission_model.objects.filter(
            competition=target.competition, team=target.team,
            frozen_at__isnull=False,
            frozen_at__lte=stamp,
            resolved_digest=models.F("frozen_verified_digest"),
            deployed_digest_verified_at__gt=stamp - FRESHNESS,
            deployed_digest_verified_at__lte=stamp,
        ).exclude(frozen_verified_digest="").order_by("-frozen_at", "-pk")[:2])
        if len(attempts) != 1:
            return None
        attempt = attempts[0]
        digest, verified_at = attempt.frozen_verified_digest, attempt.deployed_digest_verified_at
        if (attempt.resolved_digest != digest or attempt.port != target.port
                or target.protocol != "tcp"):
            return None
        if (not isinstance(attempt.extra_images, list) or not isinstance(attempt.frozen_extra_images, list)
                or len(attempt.extra_images) != len(attempt.frozen_extra_images)
                or len(attempt.frozen_extra_images) > 2):
            return None
        for reference in attempt.frozen_extra_images:
            match = PINNED_DIGEST.fullmatch(reference) if isinstance(reference, str) and len(reference) <= 500 else None
            if match is None:
                return None
            extra_digests.append(match.group(1))
        previous = TargetIdentityInterval.objects.filter(range_target=target).order_by("-valid_from", "-pk").first()
        if (previous is not None and previous.frozen_extra_digests != extra_digests
                and verified_at <= previous.verified_at):
            # A changed definition cannot reuse the old set's heartbeat.
            return None
    else:
        digest, verified_at = target.image_digest, target.verified_at
    if not DIGEST.fullmatch(digest or "") or verified_at is None:
        return None
    if verified_at > stamp or stamp - verified_at >= FRESHNESS:
        return None
    return digest, verified_at, extra_digests


@transaction.atomic
def enroll_identity(competition, *, now=None):
    stamp = require_time(now or timezone.now())
    state, _ = ScoringIdentityState.objects.get_or_create(
        competition=competition, defaults={"baseline_at": stamp, "refreshed_at": stamp},
    )
    refresh_identity(competition, now=stamp)
    state.refresh_from_db()
    return state


@transaction.atomic
def refresh_identity(competition, *, now=None):
    """Advance source truth at the observation time, without historical backfill."""
    from competitions.models import TargetAssignment
    from cyberrange.models import WireGuardProfile

    stamp = require_time(now or timezone.now())
    state = _lock_state(competition)
    if stamp < state.refreshed_at:
        raise IdentityError("Identity refresh cannot run backwards.")
    frozen_roles = TeamIdentityInterval.objects.filter(competition=competition).select_related("team")
    for identity in frozen_roles:
        team = identity.team
        if (team.competition_id != competition.pk or team.team_identifier != identity.identifier
                or team.team_type != identity.side or team.track != identity.track):
            raise IdentityError("A bulk source write changed an enrolled team's immutable scoring role.")
    revision = state.revision + 1
    teams, team_rows = {}, {}
    for team in Team.objects.filter(competition=competition, status=Team.Status.APPROVED):
        try:
            resolve_team(competition, team.team_identifier)
            enterprise = _enterprise(team)
        except IdentityError:
            continue
        if not team.cell_id:
            continue
        teams[team.pk] = team
        team_rows[team.pk] = dict(team_id=team.pk, identifier=team.team_identifier,
                                 side=team.team_type, track=team.track, enterprise=enterprise,
                                 cell_id=team.cell_id, namespace=team.namespace)
    _replace_open(TeamIdentityInterval, competition, team_rows, stamp, revision, lambda row: row.team_id)

    targets = {}
    all_targets = list(RangeApplication.objects.filter(competition=competition).select_related("team", "competition"))
    active_target_ids = set()
    for target in all_targets:
        try:
            validate_target_source(target)
        except (ValidationError, IdentityError):
            continue
        if target.team_id not in teams or not target.enabled or target.status not in {"live", "frozen", "down"}:
            continue
        if target.application in {"APP08", "APP09"} and {
            t.enterprise for t in all_targets if t.application == target.application and t.enabled
        } != {"ENT-A", "ENT-B"}:
            continue
        proof = _eligible_deployment(target, stamp)
        if proof is None:
            continue
        digest, verified_at, extra_digests = proof
        fields = dict(team_id=target.team_id, team_identifier=target.team.team_identifier,
                      target_id=target.application_instance_id, enterprise=target.enterprise,
                      application=target.application, namespace=target.namespace,
                      ip_address=str(ipaddress.ip_address(target.ip_address)), port=target.port,
                      protocol=target.protocol, frozen_digest=digest, frozen_extra_digests=extra_digests)
        existing = list(TargetIdentityInterval.objects.filter(
            competition=competition, range_target=target, valid_until__gte=stamp,
        ).order_by("-valid_from")[:2])
        current = existing[0] if len(existing) == 1 else None
        same = current is not None and all(getattr(current, name) == value for name, value in fields.items())
        end = verified_at + FRESHNESS
        if same and end >= current.valid_until:
            _advance(current, end=end, stamp=stamp, verified_at=verified_at)
        else:
            for prior in existing:
                _advance(prior, end=stamp, stamp=stamp)
            TargetIdentityInterval.objects.create(
                competition=competition, range_target=target, revision=revision, valid_from=stamp,
                valid_until=end, verified_at=verified_at, **fields,
            )
        active_target_ids.add(target.pk)
        targets[target.pk] = target
    for prior in TargetIdentityInterval.objects.filter(competition=competition, valid_until__gt=stamp).exclude(range_target_id__in=active_target_ids):
        _advance(prior, end=stamp, stamp=stamp)

    allocation_rows = {}
    duplicates = set()
    # Handshake activity is runtime liveness, not allocation authority. An idle
    # peer keeps its assigned address until revocation or reassignment.
    for profile in WireGuardProfile.objects.filter(team_id__in=teams, revoked=False).select_related("user__profile"):
        team = teams[profile.team_id]
        try:
            if "%" in profile.address:
                continue
            interface = ipaddress.ip_interface(profile.address)
            if interface.network.prefixlen != interface.max_prefixlen:
                continue
            address = str(interface.ip)
        except ValueError:
            continue
        participant = profile.user.profile
        if not participant.participant_identifier:
            continue
        fields = dict(ip_address=address, kind="participant", participant_id=participant.pk,
                      participant_identifier=participant.participant_identifier,
                      team_id=team.pk, team_identifier=team.team_identifier,
                      team_label=f"{team.team_type}-{'app' if team.track == 'application' else 'ai'}",
                      namespace=team.namespace, status="active")
        if address in allocation_rows:
            duplicates.add(address)
        allocation_rows[address] = fields
    for address in duplicates:
        allocation_rows.pop(address, None)
    for target in targets.values():
        address = str(ipaddress.ip_address(target.ip_address))
        if address in allocation_rows:
            allocation_rows.pop(address)
            duplicates.add(address)
        if address not in duplicates:
            allocation_rows[address] = dict(ip_address=address, kind="application", participant_id=None,
                participant_identifier="", team_id=target.team_id, team_identifier=target.team.team_identifier,
                team_label=f"blue-{'app' if target.team.track == 'application' else 'ai'}",
                namespace=target.namespace, status="active")
    source_allocations = [v.copy() for v in allocation_rows.values() if v["kind"] == "participant"]
    _replace_open(IPAllocation, competition, allocation_rows, stamp, revision, lambda row: row.ip_address)

    scope_rows = {}
    pairs = TargetAssignment.objects.filter(competition=competition, is_active=True).select_related("red_team", "target_team")
    permissions = list(ActionScope.objects.filter(competition=competition, active=True).select_related("approved_by", "team"))
    ambiguous_scopes = set()
    for assignment in pairs:
        actor = teams.get(assignment.red_team_id)
        if actor is None or actor.team_type != "red" or assignment.target_team_id not in teams:
            continue
        for target in targets.values():
            defender = teams[assignment.target_team_id]
            direct = target.team_id == defender.pk and actor.track == target.team.track
            managed = actor.track == defender.track == "ai" and any(
                p.team_id == defender.pk and p.target_id == target.pk and p.approved_by.is_staff
                and p.approved_by.is_active for p in permissions)
            if not direct and not managed:
                continue
            for source in source_allocations:
                if source["team_id"] == actor.pk:
                    fields = _scope_fields(actor, target, source["ip_address"], [])
                    fields["defender_team_identifier"] = defender.team_identifier
                    fields.update(target_assignment_id=assignment.pk, action_scope_id=None)
                    key = (actor.pk, target.pk, fields["source_cidr"])
                    if key in scope_rows and scope_rows[key] != fields:
                        ambiguous_scopes.add(key)
                    scope_rows[key] = fields
    for key in ambiguous_scopes:
        scope_rows.pop(key, None)
    for permission in permissions:
        actor, target = teams.get(permission.team_id), targets.get(permission.target_id)
        if (actor is None or target is None or actor.team_type != "blue" or actor.track != "ai"
                or not permission.approved_by.is_staff or not permission.approved_by.is_active
                or not isinstance(permission.allowed_actions, list)
                or not set(permission.allowed_actions).issubset(ACTIONS)):
            continue
        issued_sources = {
            row["source_cidr"] for row in scope_rows.values() if row["application_id"] == target.pk
        }
        allowed_sources = set(permission.source_cidrs) if permission.source_cidrs else issued_sources
        for source in source_allocations:
            source_cidr = f"{source['ip_address']}/{ipaddress.ip_address(source['ip_address']).max_prefixlen}"
            if source_cidr in allowed_sources:
                fields = _scope_fields(actor, target, source["ip_address"], sorted(set(permission.allowed_actions)))
                fields["defender_team_identifier"] = actor.team_identifier
                fields.update(target_assignment_id=None, action_scope_id=permission.pk)
                scope_rows[(actor.pk, target.pk, fields["source_cidr"])] = fields
    _replace_open(ScopeAssignment, competition, scope_rows, stamp, revision,
                  lambda row: (row.team_id, row.application_id, row.source_cidr))
    state.revision, state.refreshed_at = revision, stamp
    state.source_sha256 = _source_hash(competition)
    state.save(update_fields=["revision", "refreshed_at", "source_sha256"])
    return state


def _scope_fields(actor, target, address, actions):
    return dict(team_id=actor.pk, team_identifier=actor.team_identifier,
                source_cidr=f"{address}/{ipaddress.ip_address(address).max_prefixlen}",
                application_id=target.pk, target_id=target.application_instance_id,
                target_team_identifier=target.team.team_identifier, defender_team_identifier=target.team.team_identifier,
                enterprise=target.enterprise,
                application_code=target.application, namespace=target.namespace,
                target_ip=target.ip_address, allowed_actions=actions, active=True)


def _overlapping(queryset, start, end):
    return queryset.filter(valid_from__lt=end).filter(models.Q(valid_until=None) | models.Q(valid_until__gt=start))


def _cover(rows, start, end, *, exact=True):
    """Check continuous [start,end) coverage; reject overlapping ownership."""
    points = {start, end}
    for row in rows:
        points.add(max(start, row.valid_from))
        points.add(min(end, row.valid_until or end))
    points = sorted(points)
    for left, right in zip(points, points[1:]):
        if left >= right:
            continue
        matches = [r for r in rows if r.valid_from <= left and (r.valid_until is None or r.valid_until >= right)]
        if not matches or (exact and len(matches) != 1):
            raise IdentityError("Attribution is incomplete or ambiguous in the measurement window.")


@transaction.atomic
def validate_scoring_assignment(competition, team, start, end, assignment_revisions):
    start, end = require_time(start, "start"), require_time(end, "end")
    if start >= end or type(assignment_revisions) is not list or not assignment_revisions:
        raise IdentityError("A nonempty attribution window and revisions are required.")
    if any(type(v) is not int or v <= 0 for v in assignment_revisions) or len(set(assignment_revisions)) != len(assignment_revisions):
        raise IdentityError("Attribution revisions must be distinct positive integers.")
    canonical = resolve_team(competition, team.team_identifier)
    if canonical.pk != team.pk:
        raise IdentityError("Team identity changed.")
    state = _lock_state(competition)
    _check_source(state)
    if state.refreshed_at > timezone.now() or end > min(timezone.now(), state.refreshed_at + FRESHNESS):
        raise IdentityError("The window extends beyond verified identity history.")
    window_start, window_end = competition_window(competition)
    if start < max(window_start, state.baseline_at) or end > window_end:
        raise IdentityError("The window is outside verified scoring history.")
    teams = list(_overlapping(TeamIdentityInterval.objects.filter(competition=competition, team=team), start, end))
    _cover(teams, start, end)
    targets = list(_overlapping(TargetIdentityInterval.objects.filter(competition=competition, team=team), start, end))
    rows = list(teams)
    managed_scopes = list(_overlapping(ScopeAssignment.objects.filter(
        competition=competition, team=team, active=True, action_scope__isnull=False), start, end))
    if team.team_type == "blue" and not (team.track == "ai" and managed_scopes):
        # One registered scoring workload per team. Multiple simultaneous
        # replicas need one owner-issued target, not a guessed target choice.
        _cover(targets, start, end)
        rows.extend(targets)
    else:
        scopes = managed_scopes if team.team_type == "blue" else list(_overlapping(
            ScopeAssignment.objects.filter(competition=competition, team=team, active=True), start, end))
        if len({s.defender_team_identifier for s in scopes}) != 1:
            raise IdentityError("Scoring requires one unambiguous paired defender.")
        _cover(scopes, start, end, exact=False)
        target_rows = list(_overlapping(TargetIdentityInterval.objects.filter(
            competition=competition, target_id__in={s.target_id for s in scopes}), start, end))
        for scope in scopes:
            _cover([row for row in target_rows if row.target_id == scope.target_id],
                   max(start, scope.valid_from), min(end, scope.valid_until or end))
        rows.extend(scopes + target_rows)
    required = {row.revision for row in rows}
    if not required.issubset(set(assignment_revisions)):
        raise IdentityError("The evidence omits an assignment revision used in its window.")
    if max(assignment_revisions) > state.revision:
        raise IdentityError("The evidence names an unknown assignment revision.")
    return [_serialize_interval(row) for row in rows]


def _iso(value):
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _serialize_interval(row):
    result = {"revision": row.revision, "valid_from": _iso(row.valid_from), "valid_until": _iso(row.valid_until)}
    if isinstance(row, TeamIdentityInterval):
        result.update(team=row.identifier, side=row.side, track=row.track, enterprise=row.enterprise, cell_id=row.cell_id, namespace=row.namespace)
    elif isinstance(row, TargetIdentityInterval):
        result.update(target_id=row.target_id, team=row.team_identifier, enterprise=row.enterprise,
                      application=row.application, namespace=row.namespace, ip_address=row.ip_address,
                      port=row.port, protocol=row.protocol, frozen_digest=row.frozen_digest,
                      frozen_extra_digests=row.frozen_extra_digests,
                      verified_at=_iso(row.verified_at))
    elif isinstance(row, IPAllocation):
        result.update(allocation_id=str(row.allocation_id), team=row.team_identifier, source_ip=row.ip_address, kind=row.kind)
    elif isinstance(row, ScopeAssignment):
        result.update(scope_id=str(row.scope_id), team=row.team_identifier, target_id=row.target_id,
                      defender_team=row.defender_team_identifier, source_cidr=row.source_cidr,
                      allowed_actions=row.allowed_actions, active=row.active)
    return result


@transaction.atomic
def identity_snapshot(competition, *, start=None, end=None, now=None):
    stamp = require_time(now or timezone.now())
    state = _lock_state(competition)
    _check_source(state)
    window_start, window_end = competition_window(competition)
    historical = start is not None or end is not None
    if start is None and end is None:
        if not window_start <= stamp < window_end:
            raise IdentityError("The competition execution window is closed.")
        if not dt.timedelta(0) <= stamp - state.refreshed_at < FRESHNESS:
            raise IdentityError("The identity source is stale.")
        start, end = stamp, min(stamp + FRESHNESS, window_end)
    elif start is None or end is None:
        raise IdentityError("Supply both start and end.")
    start, end = require_time(start), require_time(end)
    if start >= end or start < state.baseline_at or start < window_start or end > window_end:
        raise IdentityError("The requested window is outside verified scoring history.")
    if historical and (state.refreshed_at > stamp or end > min(stamp, state.refreshed_at + FRESHNESS)):
        raise IdentityError("The historical window extends beyond verified source coverage.")
    result = {"schema_version": "scoring-identity/2.0", "revision": state.revision,
              "captured_at": _iso(stamp), "expires_at": _iso(stamp + FRESHNESS if historical else min(state.refreshed_at + FRESHNESS, window_end)),
              "historical": historical,
              "competition": {"id": competition.pk, "start": _iso(window_start), "end": _iso(window_end)}}
    for name, model in (("teams", TeamIdentityInterval), ("allocations", IPAllocation),
                        ("targets", TargetIdentityInterval), ("scopes", ScopeAssignment)):
        rows = list(_overlapping(model.objects.filter(competition=competition).order_by("valid_from", "pk"), start, end)[:5001])
        if len(rows) > 5000:
            raise IdentityError("Snapshot is too large; request a narrower window.")
        result[name] = [_serialize_interval(row) for row in rows]
    from competitions.models import ScoringExclusion, ScoringProducer
    exclusions = list(ScoringExclusion.objects.filter(competition=competition).order_by("pk")[:1001])
    if len(exclusions) > 1000:
        raise IdentityError("Exclusion export is too large; request a narrower window.")
    result["exclusions"] = [dict(id=row.pk, start=_iso(row.start), end=_iso(row.end), reason=row.reason) for row in exclusions]
    producers = list(ScoringProducer.objects.filter(competition=competition).order_by("pk")[:1001])
    if len(producers) > 1000:
        raise IdentityError("Producer registry exceeds the bounded snapshot capacity.")
    result["producers"] = [dict(producer_id=str(row.producer_id), kind=row.kind,
        team=row.team.team_identifier if row.team_id else None, valid_from=_iso(row.valid_from),
        valid_until=_iso(row.valid_until), revoked_at=_iso(row.revoked_at)) for row in producers]
    return result
