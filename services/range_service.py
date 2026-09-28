"""The bridge between this site and the cyber range.

The site decides who is in which cell (team_service.assign_cell) and which
Red team may attack whom (competitions.TargetAssignment). The range
(jengasec-cyberrange-infrastructure) needs the same facts as files its
scripts can read. This module produces them, in the exact shapes of that
repository's contracts:

    cells.yaml              scripts/cells.yaml             (ADR 0006)
    scope-assignments.csv   contracts/scope-assignments.example.csv
    allocation.csv          input to scripts/create-accounts (APP-12)

No personal data leaves through here: team identifiers, names and cells
only -- never member names, emails or phone numbers.
"""
import csv
import io

from accounts.models import Team
from competitions.constants import ENTERPRISE_CODES, Track
from competitions.models import TargetAssignment

# contracts/address-plan.md: one /24 per Red team, 10.60.<n>.0/24.
RED_RANGE = "10.60.{n}.0/24"

ALLOCATION_COLUMNS = [
    "enterprise", "cell_id", "namespace", "kind", "side", "track",
    "team_identifier", "team_name",
]
SCOPE_COLUMNS = [
    "red_team", "red_cell", "enterprise", "target_cell", "target_namespace",
    "wg_range", "round_active", "notes",
]


def enterprise_slug(enterprise):
    """'a' -> 'enta'. Empty before a team is placed."""
    return ENTERPRISE_CODES.get(enterprise, "").lower()


def namespace_for(team):
    """The team's cell as the range names it: 'enta-app03'.

    An organiser-issued `Team.namespace` wins; otherwise it is derived from
    the enterprise and cell. Empty until a cell is assigned.
    """
    if team.namespace:
        return team.namespace
    if not (team.enterprise and team.cell_id):
        return ""
    return f"{enterprise_slug(team.enterprise)}-{team.cell_id.lower()}"


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
            "enterprise": enterprise_slug(team.enterprise),
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
        yield {
            "red_team": a.red_team.team_identifier or a.red_team.team_name,
            "red_cell": a.red_team.cell_id,
            "enterprise": enterprise_slug(a.target_team.enterprise),
            "target_cell": a.target_team.cell_id,
            "target_namespace": namespace_for(a.target_team),
            "wg_range": ranges.get(a.red_team_id, ""),
            "round_active": "true" if a.is_active else "false",
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
    """scripts/cells.yaml for the range: every allocated Blue cell.

    Written by hand in the same one-line-per-cell layout as the committed
    file, so a pull request that replaces it shows a readable diff.
    """
    lines = [
        f"# Generated by `manage.py export_range --competition {competition.pk}` -- do not edit by hand.",
        "# Blue cells allocated on the competition site (ADR 0006).",
        "cells:",
    ]
    for team in allocated_teams(competition).filter(team_type=Team.TeamType.BLUE):
        kind = cell_kind(team)
        service = (
            team.application_brief.name if team.application_brief_id
            else team.responsibility or ("AI Defence cell" if kind == "ai" else team.cell_id)
        )
        lines.append(
            f"  - {{ name: {namespace_for(team)}, enterprise: {enterprise_slug(team.enterprise)}, "
            f"code: {team.cell_id}, kind: {kind}, enabled: true, service: {_yaml_str(service)}, "
            f"team: {_yaml_str(team.team_identifier)} }}"
        )
    return "\n".join(lines) + "\n"


def fill_namespaces(competition):
    """Store the derived namespace on Blue teams that have none yet.

    Teams see `Team.namespace` in their welcome pack, so it is written once
    the cell is known. Returns how many teams were updated.
    """
    updated = 0
    for team in allocated_teams(competition).filter(team_type=Team.TeamType.BLUE, namespace=""):
        team.namespace = namespace_for(team)
        team.save(update_fields=["namespace", "updated_at"])
        updated += 1
    return updated
