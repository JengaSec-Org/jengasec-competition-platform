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

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Team, UserProfile
from competitions.constants import Track
from competitions.models import ApplicationBrief, TargetAssignment
from cyberrange.models import IPAllocation, RangeApplication, ScopeAssignment, WireGuardProfile

# contracts/address-plan.md: one /24 per Red team, 10.60.<n>.0/24.
RED_RANGE = "10.60.{n}.0/24"

ALLOCATION_COLUMNS = [
    "enterprise", "cell_id", "namespace", "kind", "side", "track",
    "team_identifier", "team_name",
]
SCOPE_COLUMNS = [
    "team_id", "source_cidr", "enterprise", "application", "namespace",
    "target_ip", "active", "notes",
]

ENTERPRISE_IDS = {"a": "ENT-A", "b": "ENT-B"}
APPLICATION_CODES = tuple(f"APP{number:02d}" for number in range(1, 10))


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
    """Return a canonical application namespace, never a free-form alias."""
    return canonical_namespace(enterprise_id(team.enterprise), team.cell_id)


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
            "kind": cell_kind(team),
            "side": team.team_type,
            "track": team.track,
            "team_identifier": team.team_identifier,
            "team_name": team.team_name,
        }


def red_ranges(competition):
    """Red team pk -> its WireGuard range, numbered by team identifier.

    Deterministic, so re-exporting never renumbers a team. NET owns the
    address plan; if it changes the numbering rule, change it here too.
    """
    reds = (
        Team.objects.filter(
            competition=competition, team_type=Team.TeamType.RED, status=Team.Status.APPROVED
        )
        .order_by("team_identifier", "pk")
        .values_list("pk", flat=True)
    )
    return {pk: RED_RANGE.format(n=n) for n, pk in enumerate(reds, start=1)}


def scope_rows(competition):
    ranges = red_ranges(competition)
    assignments = (
        TargetAssignment.objects.filter(competition=competition)
        .select_related("red_team", "target_team")
        .order_by("red_team__team_identifier", "target_team__enterprise", "target_team__cell_id")
    )
    for a in assignments:
        enterprise = enterprise_id(a.target_team.enterprise)
        application = a.target_team.cell_id
        yield {
            "team_id": a.red_team.team_identifier,
            "source_cidr": ranges.get(a.red_team_id, ""),
            "enterprise": enterprise,
            "application": application,
            "namespace": namespace_for(a.target_team),
            "target_ip": _application_ip(enterprise, application),
            "active": "true" if a.is_active else "false",
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


def fill_namespaces(competition):
    """Store the derived namespace on Blue teams that have none yet.

    Teams see `Team.namespace` in their welcome pack, so it is written once
    the cell is known. Returns how many teams were updated.
    """
    updated = 0
    for team in allocated_teams(competition).filter(team_type=Team.TeamType.BLUE):
        namespace = namespace_for(team)
        if namespace and team.namespace != namespace:
            team.namespace = namespace
            team.save(update_fields=["namespace", "updated_at"])
            updated += 1
    return updated


def _application_ip(enterprise, application):
    number = int(application[-2:])
    offset = 10 if enterprise == RangeApplication.Enterprise.A else 20
    return f"10.108.0.{offset + number}"


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
        profile.save(update_fields=["participant_identifier"])
    return profile.participant_identifier


def _application_catalogue(competition):
    briefs = {
        brief.code: brief
        for brief in ApplicationBrief.objects.filter(
            competition=competition,
            track=Track.APPLICATION,
            code__in=APPLICATION_CODES,
        ).order_by("code", "pk")
    }
    missing = sorted(set(APPLICATION_CODES) - set(briefs))
    if missing:
        raise ObservabilitySyncError(
            "Application catalogue is incomplete; missing " + ", ".join(missing)
        )
    return briefs


@transaction.atomic
def sync_observability_data(competition, *, enable_optional=False):
    """Reconcile privacy-safe attribution tables from authoritative platform state.

    Re-running the operation updates existing rows rather than allocating new
    identities or addresses. It never marks a WireGuard address active before
    its owner has downloaded the profile.
    """
    briefs = _application_catalogue(competition)
    applications = {}
    for enterprise in RangeApplication.Enterprise.values:
        for code in APPLICATION_CODES:
            brief = briefs[code]
            defaults = {
                "enterprise": enterprise,
                "application": code,
                "namespace": canonical_namespace(enterprise, code),
                "service_name": brief.name,
                "ip_address": _application_ip(enterprise, code),
                "optional": brief.is_optional,
                "enabled": enable_optional or not brief.is_optional,
            }
            application, _ = RangeApplication.objects.update_or_create(
                competition=competition,
                application_instance_id=f"{enterprise}/{code}",
                defaults=defaults,
            )
            applications[(enterprise, code)] = application
            IPAllocation.objects.update_or_create(
                competition=competition,
                ip_address=application.ip_address,
                defaults={
                    "kind": IPAllocation.Kind.APPLICATION,
                    "participant": None,
                    "team": None,
                    "team_label": "",
                    "namespace": application.namespace,
                    "status": (
                        IPAllocation.Status.ACTIVE
                        if application.status == RangeApplication.Status.LIVE
                        else IPAllocation.Status.RESERVED
                    ),
                    "valid_until": None,
                },
            )

    fill_namespaces(competition)

    profile_ids = set(
        UserProfile.objects.filter(
            Q(user__wireguard_profile__team__competition=competition)
            | Q(user__team_memberships__team__competition=competition)
            | Q(user__captained_teams__competition=competition)
            | Q(role__in=[UserProfile.Role.ADMIN, UserProfile.Role.JUDGE])
        ).values_list("pk", flat=True)
    )
    for profile in UserProfile.objects.filter(pk__in=profile_ids).order_by("user_id"):
        _ensure_participant_identifier(profile)

    current_participant_allocations = []
    profiles = WireGuardProfile.objects.filter(team__competition=competition).select_related(
        "user__profile", "team"
    )
    for wireguard in profiles:
        if not wireguard.address:
            continue
        try:
            address = str(ipaddress.ip_interface(wireguard.address).ip)
        except ValueError as exc:
            raise ObservabilitySyncError(
                f"WireGuard profile {wireguard.pk} has an invalid address."
            ) from exc
        profile = wireguard.user.profile
        _ensure_participant_identifier(profile)
        status = IPAllocation.Status.RESERVED
        valid_until = None
        if wireguard.revoked:
            status = IPAllocation.Status.REVOKED
            valid_until = timezone.now()
        elif wireguard.downloaded_at:
            status = IPAllocation.Status.ACTIVE
        allocation, _ = IPAllocation.objects.update_or_create(
            competition=competition,
            ip_address=address,
            defaults={
                "kind": IPAllocation.Kind.PARTICIPANT,
                "participant": profile,
                "team": wireguard.team,
                "team_label": _team_label(wireguard.team),
                "namespace": (
                    namespace_for(wireguard.team)
                    if wireguard.team.team_type == Team.TeamType.BLUE
                    else ""
                ),
                "status": status,
                "valid_from": wireguard.issued_at,
                "valid_until": valid_until,
            },
        )
        current_participant_allocations.append(allocation.pk)
    stale = IPAllocation.objects.filter(
        competition=competition, kind=IPAllocation.Kind.PARTICIPANT
    ).exclude(pk__in=current_participant_allocations)
    stale.update(status=IPAllocation.Status.REVOKED, valid_until=timezone.now())

    ranges = red_ranges(competition)
    assignments = TargetAssignment.objects.filter(competition=competition).select_related(
        "red_team", "target_team"
    )
    for assignment in assignments:
        enterprise = enterprise_id(assignment.target_team.enterprise)
        application = applications.get((enterprise, assignment.target_team.cell_id))
        if not application:
            raise ObservabilitySyncError(
                f"Target assignment {assignment.pk} does not resolve to an application instance."
            )
        source_cidr = ranges.get(assignment.red_team_id)
        if not source_cidr:
            raise ObservabilitySyncError(
                f"Target assignment {assignment.pk} has no participant network."
            )
        ScopeAssignment.objects.update_or_create(
            target_assignment=assignment,
            defaults={
                "competition": competition,
                "team": assignment.red_team,
                "source_cidr": source_cidr,
                "application": application,
                "active": assignment.is_active,
                "valid_from": assignment.created_at,
                "valid_until": None if assignment.is_active else timezone.now(),
            },
        )

    return {
        "applications": len(applications),
        "participants": len(profile_ids),
        "ip_allocations": IPAllocation.objects.filter(competition=competition).count(),
        "scope_assignments": ScopeAssignment.objects.filter(competition=competition).count(),
    }
