"""The bridge between this site and the cyber range.

The site decides who is in which cell (team_service.assign_cell) and which
Red team may attack whom (competitions.TargetAssignment). The range
(jengasec-cyberrange-infrastructure) needs the same facts as files its
scripts can read. This module produces them, in the exact shapes of that
repository's contracts:

    cells.yaml              scripts/cells.yaml             (ADR 0006)
    scope-assignments.csv   contracts/scope-assignments.example.csv
    allocation.csv          input to scripts/create-accounts (APP-12)

The versioned observability views expose only opaque identifiers and range
coordinates. Names, email addresses, phone numbers and identity data never
leave through that interface.
"""
import csv
import io
import ipaddress
import re

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Team, TeamMember, UserProfile
from competitions.constants import Track
from competitions.models import ApplicationBrief, Competition, TargetAssignment
from cyberrange.models import IPAllocation, RangeApplication, ScopeAssignment, WireGuardProfile

ALLOCATION_COLUMNS = [
    "enterprise", "cell_id", "namespace", "service_ip", "kind", "side", "track",
    "team_identifier", "team_name",
]
SCOPE_COLUMNS = [
    "team_id", "source_cidr", "enterprise", "application", "namespace",
    "target_ip", "active", "notes",
]

ENTERPRISE_IDS = {"a": "ENT-A", "b": "ENT-B"}
APPLICATION_CODES = tuple(f"APP{number:02d}" for number in range(1, 10))
# Reserved application addresses in the shared address-plan contract. An
# explicit platform reservation takes precedence; team order never allocates IPs.
APPLICATION_RESERVATIONS = {
    "ENT-A": dict(zip(APPLICATION_CODES, (f"10.108.0.{n}" for n in range(11, 20)))),
    "ENT-B": dict(zip(APPLICATION_CODES, (f"10.108.0.{n}" for n in range(21, 30)))),
}
PARTICIPANT_RANGES = {
    (Team.TeamType.RED, Track.APPLICATION): (60, 54),
    (Team.TeamType.BLUE, Track.APPLICATION): (61, 18),
    (Team.TeamType.BLUE, Track.AI): (62, 3),
    (Team.TeamType.RED, Track.AI): (64, 6),
}


class ObservabilitySyncError(ValueError):
    """The platform cannot publish a complete, internally consistent snapshot."""


def enterprise_id(enterprise):
    """Return the shared contract identifier (`ENT-A` or `ENT-B`)."""
    return ENTERPRISE_IDS.get(enterprise, "")


def canonical_namespace(enterprise, application):
    if enterprise not in RangeApplication.Enterprise.values or application not in APPLICATION_CODES:
        return ""
    return f"jengasec-{enterprise.lower()}-{application.lower()}"


def namespace_for(team):
    """Validate the assigned namespace without overwriting an owner's value."""
    if team.enterprise not in ENTERPRISE_IDS or not re.fullmatch(r"(?:APP0[1-9]|AI0[1-3])", team.cell_id):
        return ""
    namespace = f"jengasec-ent-{team.enterprise}-{team.cell_id.lower()}"
    if team.namespace and team.namespace != namespace:
        raise ObservabilitySyncError(f"Team {team.pk} has a non-canonical assigned namespace.")
    return namespace


def cell_kind(team):
    """app | ai for Blue cells; red teams have no cell in the range."""
    if team.team_type != Team.TeamType.BLUE:
        return ""
    return "app" if team.track == Track.APPLICATION else "ai"


def allocated_teams(competition):
    """Approved teams that have a cell, in a stable order."""
    return (
        Team.objects.filter(competition=competition, status=Team.Status.APPROVED)
        .exclude(cell_id="")
        .select_related("application_brief")
        .order_by("team_type", "enterprise", "cell_id")
    )


def allocation_rows(competition):
    for team in allocated_teams(competition):
        yield {
            "enterprise": enterprise_id(team.enterprise),
            "cell_id": team.cell_id,
            "namespace": namespace_for(team) if team.team_type == Team.TeamType.BLUE else "",
            "service_ip": (
                team.service_ip
                if team.team_type == Team.TeamType.BLUE and team.track == Track.APPLICATION
                else ""
            ),
            "kind": cell_kind(team),
            "side": team.team_type,
            "track": team.track,
            "team_identifier": team.team_identifier,
            "team_name": team.team_name,
        }


def red_ranges(competition):
    """Read team networks from assigned profiles; a missing network stays absent."""
    records, _ = _participant_sources(competition)
    return _source_networks(records)


def scope_rows(competition):
    assignments = (
        ScopeAssignment.objects.filter(competition=competition)
        .select_related("team", "application")
        .order_by("team__team_identifier", "application__enterprise", "application__application")
    )
    for a in assignments:
        yield {
            "team_id": a.team.team_identifier,
            "source_cidr": a.source_cidr,
            "enterprise": a.application.enterprise,
            "application": a.application.application,
            "namespace": a.application.namespace,
            "target_ip": a.application.ip_address,
            "active": "true" if a.active else "false",
            "notes": "",
        }


def rows_to_csv(rows, columns):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def allocation_csv(competition):
    return rows_to_csv(allocation_rows(competition), ALLOCATION_COLUMNS)


def scope_csv(competition):
    return rows_to_csv(scope_rows(competition), SCOPE_COLUMNS)


def _yaml_str(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def cells_yaml(competition):
    """scripts/cells.yaml for the range: every canonical application instance.

    Written by hand in the same one-line-per-cell layout as the committed
    file, so a pull request that replaces it shows a readable diff.
    """
    lines = [
        f"# Generated by `manage.py export_range --competition {competition.pk}` -- do not edit by hand.",
        "# Canonical application instances from the observability contract.",
        "cells:",
    ]
    for application in RangeApplication.objects.filter(competition=competition):
        lines.append(
            f"  - {{ namespace: {application.namespace}, enterprise: {application.enterprise}, "
            f"application: {application.application}, reserved_ip: {application.ip_address}, "
            f"optional: {str(application.optional).lower()}, enabled: {str(application.enabled).lower()}, "
            f"service: {_yaml_str(application.service_name)} }}"
        )
    return "\n".join(lines) + "\n"


def deployment_cells_yaml(competition):
    """The existing deployment API format, read directly from assigned teams."""
    lines = ["# Assigned Blue cells for the deployment API.", "cells:"]
    for team in allocated_teams(competition).filter(team_type=Team.TeamType.BLUE):
        kind = cell_kind(team)
        service = (team.application_brief.name if team.application_brief_id else
                   team.responsibility or ("AI Defence cell" if kind == "ai" else team.cell_id))
        enterprise = enterprise_id(team.enterprise).replace("-", "").lower()
        lines.append(
            f"  - {{ name: {namespace_for(team)}, enterprise: {enterprise}, "
            f"code: {team.cell_id}, kind: {kind}, enabled: true, service: {_yaml_str(service)}, "
            f"team: {_yaml_str(team.team_identifier)}"
            f"{', service_ip: ' + _yaml_str(team.service_ip) if kind == 'app' else ''} }}"
        )
    return "\n".join(lines) + "\n"


def deployment_scope_csv(competition):
    """Keep the deployment API columns without deriving unassigned networks."""
    columns = ["red_team", "red_cell", "enterprise", "target_cell", "target_namespace",
               "target_service_ip", "wg_range", "round_active", "notes"]
    ranges = red_ranges(competition)
    rows = []
    for assignment in TargetAssignment.objects.filter(competition=competition).select_related(
            "red_team", "target_team").order_by(
            "red_team__team_identifier", "target_team__enterprise", "target_team__cell_id"):
        red, target = assignment.red_team, assignment.target_team
        source = ranges.get(red.pk, "")
        rows.append({
            "red_team": red.team_identifier or red.team_name,
            "red_cell": red.cell_id,
            "enterprise": enterprise_id(target.enterprise).replace("-", "").lower(),
            "target_cell": target.cell_id, "target_namespace": namespace_for(target),
            "target_service_ip": target.service_ip, "wg_range": source,
            "round_active": "true" if assignment.is_active and source else "false",
            "notes": "" if source else "source network unavailable",
        })
    return rows_to_csv(rows, columns)


def fill_namespaces(competition):
    """Store the derived namespace on Blue teams that have none yet.

    Teams see `Team.namespace` in their welcome pack, so it is written once
    the cell is known. Returns how many teams were updated.
    """
    updated = 0
    for team in allocated_teams(competition).filter(team_type=Team.TeamType.BLUE, namespace=""):
        namespace = namespace_for(team)
        if namespace and team.namespace != namespace:
            team.namespace = namespace
            team.save(update_fields=["namespace", "updated_at"])
            updated += 1
    return updated


def _application_ip(enterprise, application):
    return APPLICATION_RESERVATIONS.get(enterprise, {}).get(application, "")


def _team_label(team):
    if not team:
        return ""
    labels = {
        (Team.TeamType.RED, Track.APPLICATION): IPAllocation.TeamLabel.RED_APP,
        (Team.TeamType.BLUE, Track.APPLICATION): IPAllocation.TeamLabel.BLUE_APP,
        (Team.TeamType.RED, Track.AI): IPAllocation.TeamLabel.RED_AI,
        (Team.TeamType.BLUE, Track.AI): IPAllocation.TeamLabel.BLUE_AI,
    }
    return labels.get((team.team_type, team.track), "")


def _ensure_participant_identifier(profile):
    if not profile.participant_identifier:
        profile.participant_identifier = f"JS26-P-{profile.user_id:04d}"
        # UserProfile.save also changes auth groups. Issuing an opaque reference
        # must not grant or change a role.
        UserProfile.objects.filter(pk=profile.pk).update(
            participant_identifier=profile.participant_identifier
        )
    if not re.fullmatch(r"JS26-P-\d{4,}", profile.participant_identifier):
        raise ObservabilitySyncError(f"Profile {profile.pk} has an invalid participant identifier.")
    return profile.participant_identifier


def _application_catalogue(competition):
    briefs = {}
    for brief in ApplicationBrief.objects.filter(
            competition=competition,
            track=Track.APPLICATION,
            code__in=APPLICATION_CODES,
        ).order_by("code", "pk"):
        if brief.code in briefs:
            raise ObservabilitySyncError(f"Application catalogue contains duplicate {brief.code} briefs.")
        briefs[brief.code] = brief
    missing = sorted(set(APPLICATION_CODES) - set(briefs))
    if missing:
        raise ObservabilitySyncError(
            "Application catalogue is incomplete; missing " + ", ".join(missing)
        )
    return briefs


def _eligible_team(team):
    side = "B" if team.team_type == Team.TeamType.BLUE else "R"
    return (
        team.status == Team.Status.APPROVED
        and (team.team_type, team.track) in PARTICIPANT_RANGES
        and bool(re.fullmatch(rf"JS[0-9]{{2}}-{side}-[0-9]{{3,}}", team.team_identifier))
    )


def _participant_sources(competition):
    """Only existing, unambiguous owner/profile/address bindings are usable."""
    members = set(TeamMember.objects.filter(team__competition=competition)
                  .exclude(user_id=None).values_list("team_id", "user_id"))
    records, invalid, addresses = [], 0, set()
    for wireguard in WireGuardProfile.objects.filter(team__competition=competition).select_related(
        "user__profile", "team"
    ).order_by("pk"):
        team = wireguard.team
        profile = getattr(wireguard.user, "profile", None)
        if wireguard.revoked or not wireguard.user.is_active or not _eligible_team(team):
            continue
        if not profile or not wireguard.address or (
            team.captain_id != wireguard.user_id and (team.pk, wireguard.user_id) not in members
        ):
            invalid += 1
            continue
        try:
            address = ipaddress.ip_interface(wireguard.address)
        except ValueError:
            invalid += 1
            continue
        network_octet, max_team = PARTICIPANT_RANGES[(team.team_type, team.track)]
        octets = address.ip.packed
        if (address.version != 4 or address.network.prefixlen != 32
                or octets[0] != 10 or octets[1] != network_octet
                or not 1 <= octets[2] <= max_team or not 10 <= octets[3] <= 254
                or wireguard.issued_at > timezone.now()):
            invalid += 1
            continue
        value = str(address.ip)
        if value in addresses:
            raise ObservabilitySyncError("More than one participant owns an assigned address.")
        addresses.add(value)
        records.append((wireguard, profile, value, str(ipaddress.ip_network(f"{value}/24", strict=False))))
    return records, invalid


def _source_networks(records):
    networks, owners = {}, {}
    for wireguard, _, _, network in records:
        if wireguard.team.team_type != Team.TeamType.RED:
            continue
        previous = networks.setdefault(wireguard.team_id, network)
        if previous != network or owners.setdefault(network, wireguard.team_id) != wireguard.team_id:
            raise ObservabilitySyncError("Participant profiles have conflicting team network assignments.")
    return networks


def _save_changes(instance, values):
    changed = [name for name, value in values.items() if getattr(instance, name) != value]
    if changed:
        for name in changed:
            setattr(instance, name, values[name])
        instance.save(update_fields=[*changed, "updated_at"])
    return instance


def _allocation(competition, address, values):
    allocation = IPAllocation.objects.filter(competition=competition, ip_address=address).first()
    if allocation is None:
        return IPAllocation.objects.create(competition=competition, ip_address=address, **values)
    if allocation.kind != values["kind"]:
        raise ObservabilitySyncError("An address is reserved for a different allocation kind.")
    identity_changed = any(getattr(allocation, key) != values[key]
                           for key in ("participant", "team", "namespace"))
    if "valid_from" in values:
        values["valid_from"] = max(values["valid_from"], allocation.valid_from)
    if identity_changed or (allocation.status == IPAllocation.Status.REVOKED
                            and values["status"] != IPAllocation.Status.REVOKED):
        values["valid_from"] = timezone.now()
    return _save_changes(allocation, values)


@transaction.atomic
def sync_observability_data(competition, *, enable_optional=False):
    """Reconcile one competition atomically; invalid or missing bindings never grant access."""
    competition = Competition.objects.select_for_update().get(pk=competition.pk)
    briefs = _application_catalogue(competition)
    records, invalid_profiles = _participant_sources(competition)
    networks = _source_networks(records)
    now = timezone.now()
    teams = list(Team.objects.filter(competition=competition, team_type=Team.TeamType.BLUE,
                                    track=Track.APPLICATION, status=Team.Status.APPROVED))
    team_by_instance = {}
    for team in teams:
        key = (enterprise_id(team.enterprise), team.cell_id)
        if key[0] not in RangeApplication.Enterprise.values or key[1] not in APPLICATION_CODES:
            raise ObservabilitySyncError(f"Team {team.pk} has an invalid application assignment.")
        namespace_for(team)
        if key in team_by_instance:
            raise ObservabilitySyncError("More than one approved blue team owns an application instance.")
        team_by_instance[key] = team
    existing = {(a.enterprise, a.application): a for a in
                RangeApplication.objects.filter(competition=competition)}
    application_values, addresses = {}, set()
    for code, brief in briefs.items():
        enabled_values = {a.enabled for key, a in existing.items() if key[1] == code}
        if len(enabled_values) > 1 and not (enable_optional and brief.is_optional):
            raise ObservabilitySyncError(f"{code} has inconsistent activation across enterprises.")
        enabled = True if enable_optional and brief.is_optional else next(iter(enabled_values), not brief.is_optional)
        for enterprise in RangeApplication.Enterprise.values:
            key = (enterprise, code)
            previous = existing.get(key)
            team = team_by_instance.get(key)
            namespace = canonical_namespace(enterprise, code)
            if previous and previous.namespace != namespace:
                raise ObservabilitySyncError(f"{previous.application_instance_id} has an invalid namespace.")
            address = (team.service_ip if team and team.service_ip else None) or (
                previous.ip_address if previous else _application_ip(enterprise, code)
            )
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ObservabilitySyncError(f"{enterprise}/{code} has an invalid reserved address.") from exc
            if parsed.version != 4 or parsed not in ipaddress.ip_network("10.108.0.0/24") or parsed.packed[-1] in {0, 255}:
                raise ObservabilitySyncError(f"{enterprise}/{code} is outside the reserved application network.")
            if str(parsed) in addresses:
                raise ObservabilitySyncError("Application reservations contain a duplicate address.")
            addresses.add(str(parsed))
            application_values[key] = {
                "enterprise": enterprise, "application": code, "namespace": namespace,
                "service_name": brief.name, "ip_address": str(parsed),
                "optional": brief.is_optional, "enabled": enabled,
            }

    applications, current_allocations = {}, []
    if RangeApplication.objects.filter(ip_address__in=addresses).exclude(
        competition=competition
    ).exclude(competition__status__in=[Competition.Status.COMPLETED, Competition.Status.ARCHIVED]).exists():
        raise ObservabilitySyncError("Application addresses are already reserved by another current competition.")
    for key, values in application_values.items():
        application = existing.get(key)
        if application is None:
            application = RangeApplication.objects.create(
                competition=competition, application_instance_id="/".join(key), **values
            )
        else:
            _save_changes(application, values)
        applications[key] = application
        allocation = _allocation(competition, application.ip_address, {
            "kind": IPAllocation.Kind.APPLICATION, "participant": None, "team": None,
            "team_label": "", "namespace": application.namespace,
            "status": (IPAllocation.Status.ACTIVE
                       if application.enabled and application.status == RangeApplication.Status.LIVE
                       else IPAllocation.Status.RESERVED),
            "valid_until": None,
        })
        current_allocations.append(allocation.pk)

    profile_ids = set()
    approved_teams = Team.objects.filter(competition=competition, status=Team.Status.APPROVED)
    for profile in UserProfile.objects.filter(user__is_active=True).filter(
        Q(user__team_memberships__team__in=approved_teams)
        | Q(user__captained_teams__in=approved_teams)
        | Q(role__in=[UserProfile.Role.ADMIN, UserProfile.Role.JUDGE])
    ).distinct():
        _ensure_participant_identifier(profile)
        profile_ids.add(profile.pk)
    for wireguard, profile, address, _ in records:
        _ensure_participant_identifier(profile)
        profile_ids.add(profile.pk)
        allocation = _allocation(competition, address, {
            "kind": IPAllocation.Kind.PARTICIPANT, "participant": profile,
            "team": wireguard.team, "team_label": _team_label(wireguard.team),
            "namespace": (wireguard.team.namespace
                          if wireguard.team.team_type == Team.TeamType.BLUE else ""),
            "status": (IPAllocation.Status.ACTIVE if wireguard.downloaded_at
                       else IPAllocation.Status.RESERVED),
            "valid_from": wireguard.issued_at, "valid_until": None,
        })
        current_allocations.append(allocation.pk)
    IPAllocation.objects.filter(competition=competition, kind__in=[
        IPAllocation.Kind.PARTICIPANT, IPAllocation.Kind.APPLICATION,
    ]).exclude(pk__in=current_allocations).exclude(status=IPAllocation.Status.REVOKED).update(
        status=IPAllocation.Status.REVOKED, valid_until=now, updated_at=now
    )

    current_scopes, missing_networks = [], 0
    assignments = TargetAssignment.objects.filter(competition=competition).select_related("red_team", "target_team")
    for assignment in assignments:
        red, target = assignment.red_team, assignment.target_team
        application = applications.get((enterprise_id(target.enterprise), target.cell_id))
        source = networks.get(red.pk)
        if (not source or not application or not _eligible_team(red) or not _eligible_team(target)
                or red.competition_id != competition.pk or target.competition_id != competition.pk
                or red.team_type != Team.TeamType.RED or target.team_type != Team.TeamType.BLUE
                or target.track != Track.APPLICATION or not target.namespace
                or target.namespace != application.namespace
                or (target.service_ip and str(target.service_ip) != application.ip_address)):
            missing_networks += 1
            continue
        active = assignment.is_active and application.enabled
        values = {"competition": competition, "team": red, "source_cidr": source,
                  "application": application, "active": active}
        scope = ScopeAssignment.objects.filter(target_assignment=assignment).first()
        if scope is None:
            scope = ScopeAssignment.objects.create(
                target_assignment=assignment, valid_from=now,
                valid_until=None if active else now, **values
            )
        else:
            changed_binding = any(getattr(scope, name) != values[name]
                                  for name in ("team", "source_cidr", "application"))
            if changed_binding or (active and not scope.active):
                values["valid_from"] = now
            values["valid_until"] = None if active else scope.valid_until or now
            _save_changes(scope, values)
        current_scopes.append(scope.pk)
    ScopeAssignment.objects.filter(competition=competition, active=True).exclude(pk__in=current_scopes).update(
        active=False, valid_until=now, updated_at=now
    )
    return {
        "applications": len(applications), "participants": len(profile_ids),
        "ip_allocations": IPAllocation.objects.filter(competition=competition).count(),
        "scope_assignments": ScopeAssignment.objects.filter(competition=competition).count(),
        "invalid_profiles": invalid_profiles, "unresolved_scopes": missing_networks,
    }
