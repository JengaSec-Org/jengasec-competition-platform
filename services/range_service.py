"""The bridge between this site and the cyber range.

The site decides who is in which cell (team_service.assign_cell) and which
Red team may attack whom (competitions.TargetAssignment). The range
(jengasec-cyberrange-infrastructure) needs the same facts as files its
scripts can read. This module produces them, in the exact shapes of that
repository's contracts:

    cells.yaml              scripts/cells.yaml             (ADR 0006)
    scope-assignments.csv   contracts/scope-assignments.example.csv
    allocation.csv          input to scripts/create-accounts (APP-12)

and the four CSVs NET's WireGuard pipeline loads with `jengasecctl.sh bootstrap`
(scripts/bootstrap_event.py), so the VPN opens exactly what this site assigned:

    net-targets.csv                 config/csv-templates/targets.csv
    net-assignments.csv             config/csv-templates/assignments.csv
    net-infrastructure-targets.csv  config/csv-templates/infrastructure-targets.csv
    net-access-policies.csv         config/csv-templates/access-policies.csv

No personal data leaves through here: team identifiers, names and cells
only -- never member names, emails or phone numbers.
"""
import csv
import io

from django.conf import settings

from accounts.models import Team
from competitions.constants import ENTERPRISE_CODES, Track
from competitions.models import TargetAssignment

# contracts/address-plan.md: one /24 per Red team, 10.60.<n>.0/24.
RED_RANGE = "10.60.{n}.0/24"

ALLOCATION_COLUMNS = [
    "enterprise", "cell_id", "namespace", "service_ip", "kind", "side", "track",
    "team_identifier", "team_name",
]
SCOPE_COLUMNS = [
    "red_team", "red_cell", "enterprise", "target_cell", "target_namespace",
    "target_service_ip", "wg_range", "round_active", "notes",
]


def enterprise_slug(enterprise):
    """'a' -> 'enta'. Empty before a team is placed."""
    return ENTERPRISE_CODES.get(enterprise, "").lower()


def namespace_for(team):
    """The canonical infrastructure namespace for an assigned cell."""
    if not (team.enterprise and team.cell_id):
        return ""
    namespace = f"jengasec-ent-{team.enterprise}-{team.cell_id.lower()}"
    if team.namespace and team.namespace != namespace:
        raise ValueError(
            f"Team {team.pk} namespace must be {namespace} to match the cyber range."
        )
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
            "enterprise": enterprise_slug(team.enterprise),
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
            "target_service_ip": a.target_team.service_ip,
            "wg_range": ranges.get(a.red_team_id, ""),
            "round_active": "true" if a.is_active else "false",
            "notes": "",
        }


# ── NET's WireGuard bootstrap ───────────────────────────────────────────────
# Team codes are NET's: BLUE-APP-01, RED-AI-02 ... numbered per side and track in
# the same stable order the rest of this module uses, so re-exporting never
# renumbers a team. The participant API (POST /v1/participants) must send the
# same code, so both use kit_team_codes().
NET_TARGET_COLUMNS = ["target_code", "category", "owner_team", "ip", "protocol", "ports", "enabled", "namespace"]
NET_ASSIGNMENT_COLUMNS = ["red_team_code", "target_code", "starts_at", "ends_at"]
NET_INFRA_COLUMNS = ["target_code", "ip", "protocol", "ports", "enabled", "namespace"]
NET_POLICY_COLUMNS = ["subject_role", "target_code", "protocol", "port", "action", "rationale", "source_team_code"]

# The ports the applications answer on at their reserved VLAN 108 address (APP to confirm),
# and the platform VM, which carries the website (443) and the AI API (8443).
NET_APP_PORTS = getattr(settings, "NET_APP_PORTS", "80,443")
PLATFORM_ADDRESS = getattr(settings, "PLATFORM_VLAN_ADDRESS", "10.109.0.10")
AI_API_PORT = "8443"
# An AI Red team is paired with at most this many AI Blue teams (and an AI Blue
# team with at most this many AI Red teams). Matches MAX_AI_TARGETS_PER_RED and
# MAX_RED_PER_AI_TARGET in the patched bootstrap_event.py.
AI_PAIRING_LIMIT = 2


def _kit_prefix(team):
    side = "BLUE" if team.team_type == Team.TeamType.BLUE else "RED"
    return f"{side}-{'APP' if team.track == Track.APPLICATION else 'AI'}"


def _net_teams(competition):
    """Approved teams NET's VPN carries, per (side, track), in a stable order.

    Blue teams need a cell (their target); Red teams need a track.
    """
    approved = Team.objects.filter(competition=competition, status=Team.Status.APPROVED)
    blue = (approved.filter(team_type=Team.TeamType.BLUE).exclude(cell_id="")
            .order_by("enterprise", "cell_id", "pk"))
    red = approved.filter(team_type=Team.TeamType.RED).order_by("team_identifier", "pk")
    groups = {}
    for team in list(blue) + list(red):
        if team.track in (Track.APPLICATION, Track.AI):
            groups.setdefault(_kit_prefix(team), []).append(team)
    return groups


def kit_team_codes(competition):
    """Team pk -> NET team code (e.g. 'RED-APP-07'). Teams without a track get none."""
    codes = {}
    for prefix, teams in _net_teams(competition).items():
        if len(teams) > 99:
            raise ValueError(f"NET team codes have two digits; {prefix} has {len(teams)} teams")
        for n, team in enumerate(teams, start=1):
            codes[team.pk] = f"{prefix}-{n:02d}"
    return codes


def _app_target_code(team):
    return f"ENT-{team.enterprise.upper()}/{team.cell_id}"


def net_target_rows(competition):
    codes = kit_team_codes(competition)
    groups = _net_teams(competition)
    for team in groups.get("BLUE-APP", []):
        if not team.service_ip:
            raise ValueError(f"{team} has no reserved service IP: give it one before exporting")
        yield {
            "target_code": _app_target_code(team), "category": "application", "owner_team": codes[team.pk],
            "ip": team.service_ip, "protocol": "tcp", "ports": NET_APP_PORTS, "enabled": "true",
            "namespace": namespace_for(team),
        }
    # An AI Blue team's "target" is the AI API: it gives its owner and its paired
    # AI Red teams the API over WireGuard, through the kit's ordinary rules.
    for team in groups.get("BLUE-AI", []):
        yield {
            "target_code": f"AI-API/{codes[team.pk]}", "category": "ai", "owner_team": codes[team.pk],
            "ip": PLATFORM_ADDRESS, "protocol": "tcp", "ports": AI_API_PORT, "enabled": "true",
            "namespace": "",
        }


def ai_pairings(competition):
    """[(ai_red_team, ai_blue_team)], dealt round-robin, at most two either way."""
    groups = _net_teams(competition)
    reds, blues = groups.get("RED-AI", []), groups.get("BLUE-AI", [])
    if not reds or not blues:
        return []
    if len(reds) > AI_PAIRING_LIMIT * len(blues):
        raise ValueError(f"{len(reds)} AI Red teams cannot share {len(blues)} AI Blue teams, two each at most")
    if len(reds) <= len(blues):   # e.g. 3 Red, 6 Blue: every Red takes up to two Blue
        return [(reds[j % len(reds)], blue)
                for j, blue in enumerate(blues[:AI_PAIRING_LIMIT * len(reds)])]
    return [(red, blues[i % len(blues)]) for i, red in enumerate(reds)]   # more Red than Blue


def net_assignment_rows(competition):
    """Active Application Red assignments, then the AI pairings.

    Inactive assignments are left out: nothing opens until `assign_targets --activate`.
    """
    codes = kit_team_codes(competition)
    active = (TargetAssignment.objects.filter(competition=competition, is_active=True)
              .select_related("red_team", "target_team")
              .order_by("red_team__team_identifier", "target_team__enterprise", "target_team__cell_id"))
    for a in active:
        if (codes.get(a.red_team_id, "").startswith("RED-APP-")
                and codes.get(a.target_team_id, "").startswith("BLUE-APP-")):
            yield {"red_team_code": codes[a.red_team_id], "target_code": _app_target_code(a.target_team),
                   "starts_at": "", "ends_at": ""}
    for red, blue in ai_pairings(competition):
        yield {"red_team_code": codes[red.pk], "target_code": f"AI-API/{codes[blue.pk]}",
               "starts_at": "", "ends_at": ""}


def net_infrastructure_rows(competition):
    yield {"target_code": "PLATFORM", "ip": PLATFORM_ADDRESS, "protocol": "tcp",
           "ports": f"443,{AI_API_PORT}", "enabled": "true", "namespace": ""}


def net_policy_rows(competition):
    # The kit applies policies to judges and staff only; participants reach the
    # website over the venue Wi-Fi and the AI API through their AI target.
    for role in ("judge", "infrastructure_staff"):
        for port in ("443", AI_API_PORT):
            yield {"subject_role": role, "target_code": "PLATFORM", "protocol": "tcp", "port": port,
                   "action": "allow", "rationale": "website and AI API", "source_team_code": ""}


def net_bootstrap_files(competition):
    """The four CSVs, by file name. Team codes, cells and IPs only: no names."""
    return {
        "net-targets.csv": rows_to_csv(net_target_rows(competition), NET_TARGET_COLUMNS),
        "net-assignments.csv": rows_to_csv(net_assignment_rows(competition), NET_ASSIGNMENT_COLUMNS),
        "net-infrastructure-targets.csv": rows_to_csv(net_infrastructure_rows(competition), NET_INFRA_COLUMNS),
        "net-access-policies.csv": rows_to_csv(net_policy_rows(competition), NET_POLICY_COLUMNS),
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
            f"team: {_yaml_str(team.team_identifier)}"
            f"{', service_ip: ' + _yaml_str(team.service_ip) if kind == 'app' else ''} }}"
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
