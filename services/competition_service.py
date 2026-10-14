"""Competition model helpers for the team dashboards.

Everything here comes from the JengaSec Entry Guide v1.0 (JengaBank
edition): two JengaBank enterprises, two specialisations x two sides,
evidence-based scoring. The Cloud track is not offered this edition.

Static per-track copy lives here rather than in the database — it is
competition documentation, not data teams edit.
"""
from django.db.models import Q
from django.utils import timezone

from accounts.models import Team
from competitions.constants import Track

# ---------------------------------------------------------------- specs

# §2 (what each blue track does), §6 (JengaSec provides / teams provide),
# §17 (the per-team Competition Specification), §3 (red attack surfaces).
TRACK_SPECS = {
    Track.APPLICATION: {
        "summary": (
            "You build one application or service that plugs into a "
            "pre-existing enterprise environment — a building block of the "
            "enterprise, not the whole enterprise."
        ),
        "provided": [
            "Kubernetes namespace",
            "Database",
            "Ingress and endpoint",
            "Logging and monitoring",
            "CI/CD and container registry",
            "Network",
        ],
        "required": [
            "The application or service itself",
            "API contract compliance",
            "Authentication and authorisation",
            "Security controls and input validation",
            "Audit logging and telemetry",
            "Deployment configuration",
        ],
        "red_exposure": "Application attacks — web, API, authn/authz, business logic",
        "attack_surface": [
            "Web applications",
            "APIs",
            "Authentication",
            "Authorisation",
            "Business logic",
            "Injection",
            "Access control",
        ],
    },
    Track.AI: {
        "summary": (
            "You build an AI defence system that consumes enterprise "
            "telemetry and autonomously detects, analyses and responds to "
            "attacks. You do not build the enterprise itself."
        ),
        "provided": [
            "Telemetry and event feeds",
            "Event schemas",
            "Agent runtime",
            "Permitted action set",
            "APIs into the enterprise",
        ],
        "required": [
            "Detection logic",
            "Analysis and triage",
            "Autonomous response within permitted actions",
            "Agent guardrails and authorisation",
            "Decision logging",
        ],
        "red_exposure": "AI attacks — prompt manipulation, agent privilege, malicious inputs",
        "attack_surface": [
            "AI agents",
            "AI tools and workflows",
            "Prompt manipulation",
            "AI authorisation",
            "Agent privilege",
            "Malicious inputs",
            "Defensive-agent weaknesses",
        ],
    },
}

# §17 — every team submits the same artefact set.
SUBMISSION_REQUIREMENTS = [
    "Source code",
    "Container image",
    "Documentation",
    "Security report",
    "Architecture",
]

# §22 — how a team's final mark is composed. The scheme itself lives in
# `competitions.grading`, which is the one place the stage weights, the
# per-side criteria and the scorecard all read, so they cannot disagree.
# Re-exported here because the dashboards already import it from this module.
from competitions.grading import SCORE_WEIGHTS  # noqa: E402,F401

# §14 — graduated outcomes; attacks are not scored as pass/fail.
GRADUATED_OUTCOMES = [
    ("Attack not attempted", 0, 0),
    ("Attack detected", 5, 5),
    ("Attack contained", 10, 10),
    ("Attack partially successful", 15, -5),
    ("Full compromise", 25, -20),
]

# §13 — resilience earns points, it is not only about surviving attacks.
BLUE_POINT_ACTIONS = [
    ("Service availability", 10),
    ("Security control active", 10),
    ("Attack detected", 15),
    ("Attack blocked", 20),
    ("AI response successful", 20),
    ("Recovery successful", 15),
]

RED_POINT_ACTIONS = [
    ("Initial access", 10),
    ("Privilege escalation", 15),
    ("Data access", 20),
    ("Persistence", 20),
    ("Objective achieved", 30),
]

# §20 — participants get a delayed view; admins see real time.
# How far behind the participants' view runs. The Guide said thirty; set to
# five on 14 October so a team can see the effect of its own work inside the
# window it is working in, which thirty minutes did not allow. Staff and
# judges always see live.
LEADERBOARD_DELAY_MINUTES = 5


# ---------------------------------------------------------------- helpers


def team_for(user, team_type):
    """The user's team on one side of the competition, or None.

    Reuses the membership resolver in submission_service (captain,
    linked account, or matching member email) so there is one rule.
    """
    from services.submission_service import teams_for

    return (
        teams_for(user)
        .filter(team_type=team_type)
        .select_related("competition")
        .first()
    )


def spec_for(team):
    """The Competition Specification block for a team's track."""
    if not team or not team.track:
        return None
    return TRACK_SPECS.get(team.track)


def scenarios_for(team):
    """Active attack scenarios relevant to a team's track."""
    from competitions.models import AttackScenario

    if not team or not team.track:
        return AttackScenario.objects.none()
    return AttackScenario.objects.filter(track=team.track, is_active=True)


def targets_for(red_team):
    """Blue teams this red team is cleared to attack."""
    from competitions.models import TargetAssignment

    if not red_team:
        return TargetAssignment.objects.none()
    return TargetAssignment.objects.filter(red_team=red_team).select_related(
        "target_team", "competition"
    )


def availability_band(percentage):
    """§15 — availability rating bands.

    Returns (label, badge_colour). None percentage means not yet measured.
    """
    if percentage is None:
        return ("Not measured", "muted")
    if percentage >= 99:
        return ("Excellent", "green")
    if percentage >= 95:
        return ("Good", "green")
    if percentage >= 90:
        return ("Partial", "gold")
    return ("Poor", "red")


def scorecard(team):
    """The team's mark, out of 100, in the three published stages.

    Structure comes from `competitions.grading`, so the scorecard a team
    watches during the event and the criteria on its Info page are the
    same list — they used to be two hardcoded tables that had already
    drifted apart.

    Values stay None until the scoring engine fills them in; the template
    renders whatever this returns, so only the body below changes.
    """
    from competitions import grading
    from services import scoring_service

    awarded = {}
    for stage in scoring_service.breakdown(team):
        for event in stage["events"]:
            awarded[(event.stage, event.criterion)] = event.marks
    stage_totals = scoring_service.stage_totals(team)

    sections = []
    for stage in grading.stages(team):
        rows = [
            {
                "label": row["label"],
                "score": awarded.get((stage["key"], row["key"])),
                "max": row["marks"],
            }
            for row in stage["rows"]
        ]
        if not rows:
            # Entry is one mark carried in from the proposal or the
            # qualifier, not a list of criteria scored here.
            rows = [{
                "label": stage["summary"],
                "score": stage_totals.get(stage["key"]) or None,
                "max": stage["weight"],
            }]
        sections.append({"name": stage["name"], "rows": rows})

    total_max = sum(row["max"] for section in sections for row in section["rows"])
    total = sum(stage_totals.values())
    marked = any(row["score"] is not None for s in sections for row in s["rows"])
    return {
        "sections": sections,
        "total": total if marked else None,
        "total_max": total_max,
        "pending": not marked,
        "last_updated": timezone.now(),
    }


# ---------------------------------------------------------------- phases


def current_phase(team):
    """The phase whose dashboard a team should land on."""
    from competitions.models import Competition

    if not team or not team.competition:
        return Competition.Phase.REGISTRATION
    phase = team.competition.phase
    # Judging is organiser-side; teams keep seeing the live dashboard.
    if phase == Competition.Phase.JUDGING:
        return Competition.Phase.LIVE
    return phase


def phase_rail(team):
    """Registration/Build/Live rail state for the phase indicator."""
    from competitions.models import Competition

    active = current_phase(team)
    rail = []
    for phase in Competition.TEAM_PHASES:
        rail.append(
            {
                "value": phase,
                "label": Competition.Phase(phase).label,
                "reached": team.competition.phase_reached(phase) if team and team.competition else False,
                "current": phase == active,
            }
        )
    return rail


def can_view_phase(team, phase):
    """Teams may revisit reached phases, but not ones that haven't started."""
    if not team or not team.competition:
        return False
    return team.competition.phase_reached(phase)


# ---------------------------------------------------------------- briefs


def briefs_for(team):
    """Open application briefs a blue team may propose against.

    Filtered to the team's enterprise when one is set, so a team is not
    offered components from the other enterprise.
    """
    from competitions.models import ApplicationBrief

    if not team or not team.competition:
        return ApplicationBrief.objects.none()
    queryset = ApplicationBrief.objects.filter(
        competition=team.competition, is_open=True
    )
    # Briefs are enterprise-agnostic; only a deliberately pinned one is
    # filtered out for a team already placed in the other instance.
    if team.enterprise:
        queryset = queryset.filter(
            Q(enterprise="") | Q(enterprise=team.enterprise)
        )
    return queryset


def can_propose(team, brief):
    """A team may propose while the proposal window is open and the cap holds.

    The gate is the **proposal deadline**, not the registration one. They
    are separate dates in the Entry Guide and on the site -- registration
    closed on the Tuesday, proposals ran past it -- and tying proposing to
    `registration_open` meant that the moment registration shut, an
    already-registered team could no longer file the proposal it had been
    told to file. Reopening proposals then required reopening registration
    to new teams, which is not the same decision at all.

    Also honours the organiser's Close Submissions switch, so shutting the
    window actually stops proposals rather than only hiding the button.
    """
    from services.submission_service import proposal_type_name

    if not team or not brief:
        return False
    if brief.competition_id != team.competition_id:
        return False
    settings_row = team.competition.settings
    if not settings_row.submissions_open:
        return False
    # Whatever deadline governs this team's proposal type, by the same
    # resolver the upload path uses, so the button and the upload agree.
    type_name = proposal_type_name(team)
    if type_name:
        deadline = settings_row.deadline_for_type(type_name)
        if deadline and timezone.now() > deadline:
            return False
    return brief.is_open and not brief.is_full


def proposal_for(team):
    """The team's proposal submission, if it has one."""
    from submissions.models import Submission

    if not team:
        return None
    from services.submission_service import PROPOSAL_TYPES

    return (
        Submission.objects.filter(team=team, submission_type__name__in=PROPOSAL_TYPES)
        .select_related("application_brief", "submission_type")
        .first()
    )


def red_qualifier_locked(team):
    """Non-AI red teams face an elimination challenge, format still TBA."""
    if not team or team.team_type != Team.TeamType.RED:
        return False
    return team.track != Track.AI


def enterprise_peers(team):
    """Other blue teams inside the same enterprise — the shared foundation."""
    if not team or not team.enterprise:
        return Team.objects.none()
    return (
        Team.objects.filter(
            competition=team.competition,
            enterprise=team.enterprise,
            team_type=Team.TeamType.BLUE,
        )
        .exclude(pk=team.pk)
        .order_by("cell_id", "team_name")
    )


# ---------------------------------------------------------------- key dates


def key_dates(competition, team=None):
    """The published timeline, labelled, for display.

    One shape serves the public landing page, the team dashboards and the
    My Team panel, so the dates cannot disagree between them -- which is
    how the dashboards came to show `competition.end_date` under the
    heading "registration closes".

    Rows a team has nothing to do with are dropped: Application Red files
    no proposal (`Team.submits_proposal`), so it is not told when
    proposals are due.
    """
    from django.utils import timezone

    row = competition.settings
    now = timezone.now()

    planned = [
        ("rsvp", "HTB qualifier RSVP closes", row.red_rsvp_closes_at),
        ("registration", "Registration closes", row.registration_deadline),
        ("roster", "Rosters and details complete", row.roster_deadline),
        ("red_roster", "Your details complete", row.red_roster_deadline),
        ("proposal", "Proposals due", row.proposal_deadline),
        ("qualifier", "HTB qualifier opens", row.red_qualifier_opens_at),
        ("qualifier_end", "HTB qualifier closes", row.red_qualifier_closes_at),
        ("build", "Build ends", row.build_deadline),
        ("submission", "Documents due", row.submission_deadline),
    ]
    QUALIFIER_ROWS = {"rsvp", "qualifier", "qualifier_end", "red_roster"}
    if team is not None:
        # A team is only shown the route it is actually on. Application Red
        # files no proposal: it RSVPs, plays the qualifier, and only then
        # registers -- so the platform registration deadline is not its
        # gate and showing it would send teams at the wrong thing.
        if team.submits_proposal:
            planned = [r for r in planned if r[0] not in QUALIFIER_ROWS]
        else:
            # Their own details date replaces the general one when set,
            # because they register after qualifying rather than before.
            drop = {"proposal", "registration"}
            if row.red_roster_deadline:
                drop.add("roster")
            planned = [r for r in planned if r[0] not in drop]

    dates = []
    for kind, label, when in planned:
        if when is None:
            continue
        dates.append({
            "kind": kind,
            "label": label,
            "when": when,
            "passed": when < now,
            # The next deadline still ahead, for highlighting one row.
            "next": False,
        })

    # Chronological, not the order the fields happen to be declared in:
    # the roster deadline sits between registration and the build, but the
    # qualifier rows were listed after it, so a reader saw Monday above
    # Thursday and had to re-sort the list in their head.
    dates.sort(key=lambda d: d["when"])

    upcoming = [d for d in dates if not d["passed"]]
    if upcoming:
        upcoming[0]["next"] = True
    return dates
