from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render

from accounts.models import Team
from accounts.roles import BLUE_TEAM, JUDGE, PARTNER, RED_TEAM, user_in_role
from judging.models import Evaluation
from submissions.models import Submission


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
    return render(request, "dashboard/no_role.html")


@user_passes_test(lambda u: u.is_active and (u.is_staff or u.is_superuser))
def admin_dashboard(request):
    """Admin command center — live database metrics."""
    pending = Evaluation.objects.filter(
        status__in=Evaluation.IN_PROGRESS_STATUSES
    ).count()
    completed = Evaluation.objects.filter(
        status__in=Evaluation.DONE_STATUSES
    ).count()
    latest = Submission.objects.select_related(
        "team", "competition", "submission_type"
    )[:8]

    context = {
        "stats": [
            {"label": "Total Teams", "value": Team.objects.count(), "tag": "TEAMS_REGISTERED"},
            {"label": "Total Submissions", "value": Submission.objects.count(), "tag": "DOCS_RECEIVED"},
            {"label": "Pending Reviews", "value": pending, "tag": "AWAITING_JUDGES"},
            {"label": "Completed Reviews", "value": completed, "tag": "EVALUATIONS_DONE"},
        ],
        "activity_headers": ["Team", "Document", "Competition", "Submitted", "Status"],
        "activity_rows": [
            [
                s.team.team_name,
                s.submission_type.name,
                s.competition.name,
                s.submitted_at.strftime("%Y-%m-%d %H:%M") if s.submitted_at else "—",
                s.get_status_display(),
            ]
            for s in latest
        ],
    }
    return render(request, "dashboard/main_dashboard.html", context)


def _role_view(role, template):
    """Build a login-protected view locked to one role group."""

    @login_required
    def view(request):
        if not user_in_role(request.user, role):
            return redirect("dashboard:main_dashboard")
        return render(request, template)

    return view


blue_dashboard = _role_view(BLUE_TEAM, "dashboard/blue_dashboard.html")
red_dashboard = _role_view(RED_TEAM, "dashboard/red_dashboard.html")


@login_required
def insights_dashboard(request):
    """Judge/partner insights dashboard — live review-queue metrics."""
    user = request.user
    if not (user_in_role(user, JUDGE) or user_in_role(user, PARTNER) or user.is_staff):
        return redirect("dashboard:main_dashboard")

    my_evals = Evaluation.objects.filter(judge=user)
    context = {
        "assigned_count": my_evals.filter(
            status__in=Evaluation.IN_PROGRESS_STATUSES
        ).count(),
        "completed_count": my_evals.filter(
            status__in=Evaluation.DONE_STATUSES
        ).count(),
    }
    return render(request, "dashboard/insights_dashboard.html", context)
