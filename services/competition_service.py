"""Competition model helpers for the team dashboards.

Everything here comes from the JengaSec Final Competition Overview
(Aug 2026): two enterprises, three tracks, specialised blue and red
teams, evidence-based scoring.

Static per-track copy lives here rather than in the database — it is
competition documentation, not data teams edit.
"""
from django.utils import timezone

from accounts.models import Team
from competitions.constants import Track

# ---------------------------------------------------------------- specs

# §2 (what each blue track does), §6 (JengaSec provides / teams provide),
# §17 (the per-team Competition Specification), §3 (red attack surfaces).
TRACK_SPECS = {
    Track.CLOUD: {
        "summary": (
            "You own the cloud portion of the enterprise. You build and "
            "configure the cloud environment — you do not build the "
            "applications that run on it."
        ),
        "provided": [
            "Cloud account or project",
            "IAM starting configuration",
            "Networking requirements",
            "Enterprise requirements",
            "Monitoring and logging foundation",
        ],
        "required": [
            "Deploy the required cloud services",
            "IAM and access boundaries",
            "Network security controls",
            "Monitoring and alerting",
            "Security hardening of workloads",
        ],
        "red_exposure": "Cloud attacks — IAM, configuration, exposed services, storage",
        "attack_surface": [
            "IAM and privilege escalation",
            "Cloud configuration",
            "Exposed services",
            "Storage",
            "Network controls",
            "Cloud workloads",
        ],
    },
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

# §22 — how a blue team's final score is composed.
SCORE_WEIGHTS = [
    ("Technical performance", 40),
    ("Security and resilience", 25),
    ("Documentation", 15),
    ("AI defence", 15),
    ("Human evaluation", 5),
]

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
LEADERBOARD_DELAY_MINUTES = 30


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
    """§18 — the blue team scorecard structure, out of 100.

    Values stay None until the Part II scoring engine (CompetitionEvent →
    ScoringRule → ScoreEvent → TeamScore) is built. Replace the body of
    this function with a ScoreEvent aggregation; the templates already
    render whatever it returns.

    Section maxima: Security 40 · Resilience 35 · Documentation 25.
    """
    sections = [
        {
            "name": "Security",
            "rows": [
                {"label": "Authentication", "score": None, "max": 10},
                {"label": "Authorisation", "score": None, "max": 10},
                {"label": "Input validation", "score": None, "max": 10},
                {"label": "Logging", "score": None, "max": 10},
            ],
        },
        {
            "name": "Resilience",
            "rows": [
                {"label": "Attack scenario 01", "score": None, "max": 15},
                {"label": "Attack scenario 02", "score": None, "max": 10},
                {"label": "Availability", "score": None, "max": 10},
            ],
        },
        {
            "name": "Documentation",
            "rows": [
                {"label": "Architecture", "score": None, "max": 15},
                {"label": "Security report", "score": None, "max": 10},
            ],
        },
    ]
    total_max = sum(row["max"] for section in sections for row in section["rows"])
    return {
        "sections": sections,
        "total": None,
        "total_max": total_max,
        "pending": True,
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
    if team.enterprise:
        queryset = queryset.filter(enterprise=team.enterprise)
    return queryset


def can_propose(team, brief):
    """A team may propose while registration is open and the cap holds.

    Also honours the organiser's Close Submissions switch, so shutting the
    window actually stops proposals rather than only hiding the button.
    """
    if not team or not brief:
        return False
    if brief.competition_id != team.competition_id:
        return False
    if not team.competition.registration_open:
        return False
    if not team.competition.settings.submissions_open:
        return False
    return brief.is_open and not brief.is_full


def proposal_for(team):
    """The team's proposal submission, if it has one."""
    from submissions.models import Submission

    if not team:
        return None
    return (
        Submission.objects.filter(team=team, submission_type__name="Proposal")
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
