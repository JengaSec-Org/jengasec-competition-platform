from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render

from accounts.roles import BLUE_TEAM, JUDGE, PARTNER, RED_TEAM, user_in_role


@login_required
def main_dashboard(request):
    """Post-login entry point: route each user to their role's dashboard.

    admin -> admin dashboard, blue_team -> blue dashboard,
    red_team -> red dashboard, judge/partner -> insights dashboard.
    """
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
    """Admin command center. Placeholder metrics until services land."""
    context = {
        "stats": [
            {"label": "Total Teams", "value": 0, "tag": "TEAMS_REGISTERED"},
            {"label": "Total Submissions", "value": 0, "tag": "DOCS_RECEIVED"},
            {"label": "Pending Reviews", "value": 0, "tag": "AWAITING_JUDGES"},
            {"label": "Completed Reviews", "value": 0, "tag": "EVALUATIONS_DONE"},
        ],
        "activity_headers": ["Team", "Document", "Type", "Submitted", "Status"],
        "activity_rows": [],
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
    """Judge/partner insights dashboard."""
    user = request.user
    if not (user_in_role(user, JUDGE) or user_in_role(user, PARTNER) or user.is_staff):
        return redirect("dashboard:main_dashboard")
    return render(request, "dashboard/insights_dashboard.html")
