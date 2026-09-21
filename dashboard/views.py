"""Role and phase aware dashboards.

The competition runs Registration -> Build -> Live. A team lands on the
dashboard for the competition's current phase and may look back at
phases already reached (read-only). Reference material lives in the
Info section rather than cluttering the working dashboards.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404
from django.shortcuts import redirect, render

from accounts.models import Team
from accounts.roles import BLUE_TEAM, JUDGE, PARTNER, RED_TEAM, user_in_role
from competitions.models import Competition
from judging.models import Evaluation
from services import competition_service as comp
from services import submission_service
from services.submission_service import PROPOSAL_TYPES
from services.audit_service import recent as audit_recent
from submissions.models import Submission

PHASE_TEMPLATES = {
    Team.TeamType.BLUE: {
        Competition.Phase.REGISTRATION: "dashboard/blue/registration.html",
        Competition.Phase.BUILD: "dashboard/blue/build.html",
        Competition.Phase.LIVE: "dashboard/blue/live.html",
    },
    Team.TeamType.RED: {
        Competition.Phase.REGISTRATION: "dashboard/red/registration.html",
        Competition.Phase.BUILD: "dashboard/red/build.html",
        Competition.Phase.LIVE: "dashboard/red/live.html",
    },
}


@login_required
def main_dashboard(request):
    """Post-login entry point: route each user to their role's dashboard."""
    user = request.user
    if user.is_staff or user.is_superuser:
        return redirect("dashboard:admin_dashboard")
    if user_in_role(user, BLUE_TEAM):
        return redirect("dashboard:blue_dashboard")
    if user_in_role(user, RED_TEAM):
        return redirect("dashboard:red_dashboard")
    if user_in_role(user, JUDGE) or user_in_role(user, PARTNER):
        return redirect("dashboard:insights_dashboard")
    # A competitor with no team yet: their captain's invitation is the way
    # in (Entry Guide s.6), so send them where it will appear.
    return redirect("accounts:choose_team")


@user_passes_test(lambda u: u.is_active and (u.is_staff or u.is_superuser))
def admin_dashboard(request):
    """Admin command center — live counts straight off the database."""
    proposals = Submission.objects.filter(submission_type__name__in=PROPOSAL_TYPES)
    pending = Evaluation.objects.filter(
        status__in=Evaluation.IN_PROGRESS_STATUSES
    ).count()
    completed = Evaluation.objects.filter(status__in=Evaluation.DONE_STATUSES).count()
    latest = Submission.objects.select_related(
        "team", "competition", "submission_type"
    )[:8]

    context = {
        "stats": [
            {"label": "Total Teams", "value": Team.objects.count(), "tag": "TEAMS_REGISTERED"},
            {"label": "Proposals Received", "value": proposals.count(), "tag": "REGISTRATION"},
            {"label": "Total Submissions", "value": Submission.objects.count(), "tag": "DOCS_RECEIVED"},
            {"label": "Pending Reviews", "value": pending, "tag": "AWAITING_JUDGES"},
            {"label": "Completed Reviews", "value": completed, "tag": "EVALUATIONS_DONE"},
        ],
        "recent_audit": audit_recent(8),
        "activity_headers": ["Team", "Document", "Competition", "Submitted", "Status"],
        "activity_rows": [
            [
                submission.team.team_name,
                submission.submission_type.name,
                submission.competition.name,
                submission.submitted_at.strftime("%Y-%m-%d %H:%M")
                if submission.submitted_at
                else "—",
                submission.get_status_display(),
            ]
            for submission in latest
        ],
    }
    return render(request, "dashboard/main_dashboard.html", context)


# ---------------------------------------------------------------- shared


def _base_context(team, team_type, phase):
    """Context every phase dashboard needs."""
    active = comp.current_phase(team)
    return {
        "team": team,
        "spec": comp.spec_for(team),
        "phase": phase,
        "active_phase": active,
        "read_only": phase != active,
        "phase_rail": comp.phase_rail(team) if team else [],
        "is_blue": team_type == Team.TeamType.BLUE,
    }


def _resolve_phase(request, team, requested):
    """Which phase to render, or None to redirect back to the current one.

    An unrecognised URL segment is a 404, not a 500 — `Competition.Phase(x)`
    raises ValueError on junk, which would render a debug stack trace.
    """
    active = comp.current_phase(team)
    if requested is None:
        return active
    if requested not in Competition.Phase.values:
        raise Http404("Unknown competition phase")
    if requested == active:
        return active
    if comp.can_view_phase(team, requested):
        return requested
    messages.info(
        request,
        f"The {Competition.Phase(requested).label} phase hasn't started yet.",
    )
    return None


def _blue_context(request, team, phase):
    """Phase-specific context for a blue team."""
    if phase == Competition.Phase.REGISTRATION:
        briefs = list(comp.briefs_for(team))
        return {
            "briefs": briefs,
            "proposal": comp.proposal_for(team),
            "registration_open": team.competition.registration_open,
            "deadline": team.competition.end_date,
        }
    if phase == Competition.Phase.BUILD:
        submissions = Submission.objects.filter(team=team).select_related(
            "submission_type", "application_brief"
        )
        by_type = {s.submission_type.name: s for s in submissions}
        return {
            "proposal": comp.proposal_for(team),
            "deliverables": [
                {"name": name, "submission": by_type.get(name)}
                for name in (
                    submission_service.proposal_type_name(team),
                    "Blue Team Documentation",
                    "Supporting Evidence",
                )
                if name
            ],
            "submission_requirements": comp.SUBMISSION_REQUIREMENTS,
            "submissions": submissions,
        }
    # Live
    return {
        "scorecard": comp.scorecard(team),
        "submissions": Submission.objects.filter(team=team).select_related(
            "submission_type"
        ),
        "attackers": team.attacker_assignments.select_related("red_team"),
        "peers": comp.enterprise_peers(team),
        "delay_minutes": comp.LEADERBOARD_DELAY_MINUTES,
        "submission_requirements": comp.SUBMISSION_REQUIREMENTS,
    }


def _red_context(request, team, phase):
    """Phase-specific context for a red team."""
    if phase == Competition.Phase.REGISTRATION:
        return {
            "qualifier_locked": comp.red_qualifier_locked(team),
            "proposal": comp.proposal_for(team),
            "registration_open": team.competition.registration_open,
            "deadline": team.competition.end_date,
        }
    if phase == Competition.Phase.BUILD:
        return {
            "qualifier_locked": comp.red_qualifier_locked(team),
            "targets": comp.targets_for(team),
        }
    # Live
    targets = comp.targets_for(team)
    return {
        "targets": targets,
        "engagement_open": targets.filter(is_active=True).exists(),
        "submissions": Submission.objects.filter(team=team).select_related(
            "submission_type"
        ),
        "delay_minutes": comp.LEADERBOARD_DELAY_MINUTES,
    }


def _phase_view(team_type, role, context_builder, empty_template):
    """Build a login-protected, role-gated, phase-aware dashboard view."""

    @login_required
    def view(request, phase=None):
        if not user_in_role(request.user, role):
            return redirect("dashboard:main_dashboard")

        team = comp.team_for(request.user, team_type)
        if team is None:
            return render(request, empty_template, {"team": None})

        resolved = _resolve_phase(request, team, phase)
        if resolved is None:
            return redirect(
                "dashboard:blue_dashboard"
                if team_type == Team.TeamType.BLUE
                else "dashboard:red_dashboard"
            )

        context = _base_context(team, team_type, resolved)
        context.update(context_builder(request, team, resolved))
        return render(request, PHASE_TEMPLATES[team_type][resolved], context)

    return view


blue_dashboard = _phase_view(
    Team.TeamType.BLUE, BLUE_TEAM, _blue_context, "dashboard/blue/no_team.html"
)
red_dashboard = _phase_view(
    Team.TeamType.RED, RED_TEAM, _red_context, "dashboard/red/no_team.html"
)


# ---------------------------------------------------------- event-time pages


def _require_live(request, team, team_type):
    """Live Status and Target Access only exist during the competition.

    Returns a redirect when the phase isn't live, else None. Enforced
    server-side so the URLs can't be reached by typing them early.
    """
    home = (
        "dashboard:blue_dashboard"
        if team_type == Team.TeamType.BLUE
        else "dashboard:red_dashboard"
    )
    if team is None:
        return redirect(home)
    if comp.current_phase(team) != Competition.Phase.LIVE:
        messages.info(
            request, "That opens when the competition goes live."
        )
        return redirect(home)
    return None


@login_required
def blue_live_status(request):
    """How a blue team's service is holding up while it's under attack."""
    if not user_in_role(request.user, BLUE_TEAM):
        return redirect("dashboard:main_dashboard")

    team = comp.team_for(request.user, Team.TeamType.BLUE)
    blocked = _require_live(request, team, Team.TeamType.BLUE)
    if blocked:
        return blocked

    # The only endpoint recorded today is the one red teams are pointed at.
    assignments = team.attacker_assignments.select_related("red_team")
    endpoint = next(
        (a.endpoint for a in assignments if a.endpoint), ""
    )
    availability = None  # supplied by the monitoring layer (§15)
    band, band_colour = comp.availability_band(availability)

    context = {
        "team": team,
        "is_blue": True,
        "endpoint": endpoint,
        "availability": availability,
        "availability_band": band,
        "availability_colour": band_colour,
        "attacker_count": assignments.count(),
        "active_attackers": sum(1 for a in assignments if a.is_active),
        "controls": [
            "Authentication",
            "Authorisation",
            "Input validation",
            "Audit logging",
            "Rate limiting",
        ],
        "delay_minutes": comp.LEADERBOARD_DELAY_MINUTES,
    }
    return render(request, "dashboard/blue/live_status.html", context)


@login_required
def red_target_access(request):
    """Connection details for the targets a red team is cleared to attack."""
    if not user_in_role(request.user, RED_TEAM):
        return redirect("dashboard:main_dashboard")

    team = comp.team_for(request.user, Team.TeamType.RED)
    blocked = _require_live(request, team, Team.TeamType.RED)
    if blocked:
        return blocked

    targets = comp.targets_for(team)
    context = {
        "team": team,
        "is_blue": False,
        "targets": targets,
        "active_count": targets.filter(is_active=True).count(),
        "spec": comp.spec_for(team),
    }
    return render(request, "dashboard/red/target_access.html", context)


# ---------------------------------------------------------------- info


@login_required
def info(request):
    """Role-aware reference material — the competition handbook."""
    user = request.user
    is_red = user_in_role(user, RED_TEAM)
    team_type = Team.TeamType.RED if is_red else Team.TeamType.BLUE
    team = comp.team_for(user, team_type)

    context = {
        "team": team,
        "spec": comp.spec_for(team),
        "scenarios": comp.scenarios_for(team),
        "graduated_outcomes": comp.GRADUATED_OUTCOMES,
        "delay_minutes": comp.LEADERBOARD_DELAY_MINUTES,
    }

    if is_red:
        context["red_actions"] = comp.RED_POINT_ACTIONS
        return render(request, "dashboard/info_red.html", context)

    context.update(
        {
            "blue_actions": comp.BLUE_POINT_ACTIONS,
            "score_weights": comp.SCORE_WEIGHTS,
            "objectives": team.objectives.all() if team else [],
            "availability_bands": [
                ("99–100%", "Excellent"),
                ("95–99%", "Good"),
                ("90–95%", "Partial"),
                ("Below 90%", "Poor"),
            ],
        }
    )
    return render(request, "dashboard/info_blue.html", context)


@login_required
def insights_dashboard(request):
    """Judge/partner insights dashboard — the signed-in judge's real queue."""
    user = request.user
    if not (user_in_role(user, JUDGE) or user_in_role(user, PARTNER) or user.is_staff):
        return redirect("dashboard:main_dashboard")

    mine = Evaluation.objects.filter(judge=user).select_related(
        "submission__team", "submission__submission_type", "rubric"
    )
    assigned = mine.filter(status__in=Evaluation.IN_PROGRESS_STATUSES)
    completed = mine.filter(status__in=Evaluation.DONE_STATUSES)

    scored = [e.weighted_total for e in completed if e.weighted_total is not None]
    average = round(sum(scored) / len(scored), 1) if scored else None

    context = {
        "assigned_count": assigned.count(),
        "completed_count": completed.count(),
        "average_score": average,
        "review_headers": ["Team", "Document", "Rubric", "Status"],
        "review_rows": [
            [
                evaluation.submission.team.team_name,
                evaluation.submission.submission_type.name,
                evaluation.rubric.title,
                evaluation.get_status_display(),
            ]
            for evaluation in assigned[:10]
        ],
    }
    return render(request, "dashboard/insights_dashboard.html", context)
