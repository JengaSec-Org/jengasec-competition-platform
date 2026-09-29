from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render

from accounts.roles import BLUE_TEAM, JUDGE, PARTNER, RED_TEAM, user_in_role

from judging.models import Evaluation

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


    evaluations = Evaluation.objects.filter(judge=request.user)

    assigned_reviews_count = evaluations.filter(
        status__in=Evaluation.IN_PROGRESS_STATUSES
    ).count()

    completed_reviews_count = evaluations.filter(
        status__in=Evaluation.DONE_STATUSES
    ).count()

    context = {
        "assigned_reviews_count": assigned_reviews_count,
        "completed_reviews_count": completed_reviews_count,
        "active_tab": "insights",
    }

    
    return render(request, "dashboard/insights_dashboard.html", context)






from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from accounts.models import UserProfile
from submissions.models import Submission
from rubrics.models import Rubric
from judging.models import Evaluation   # <-- now an explicit cross-app import

User = get_user_model()

@login_required
def assign_submissions(request):
    """
    Assign submitted work to judges. Creating an Evaluation here auto-triggers
    AI evaluation via the post_save signal in judging/signals.py -- no extra
    code needed here for that part.
    """
    if request.method == "POST":
        judge_id = request.POST.get("judge_id")
        submission_ids = request.POST.getlist("submission_ids")

        judge = User.objects.filter(pk=judge_id, profile__role=UserProfile.Role.JUDGE).first()
        if not judge:
            messages.error(request, "Select a valid judge.")
            return redirect("dashboard:assign_submissions")

        if not submission_ids:
            messages.error(request, "Select at least one submission to assign.")
            return redirect("dashboard:assign_submissions")

        created, skipped_no_rubric, skipped_existing = 0, [], []

        for sub_id in submission_ids:
            submission = Submission.objects.filter(pk=sub_id).first()
            if submission is None:
                continue

            rubric = Rubric.objects.filter(
                competition=submission.competition,
                submission_type=submission.submission_type,
                is_active=True,
            ).first()
            if rubric is None:
                skipped_no_rubric.append(str(submission))
                continue

            _, was_created = Evaluation.objects.get_or_create(
                submission=submission, judge=judge, rubric=rubric,
            )
            if was_created:
                created += 1
            else:
                skipped_existing.append(str(submission))

        if created:
            messages.success(
                request,
                f"Assigned {created} submission(s) to {judge.username}. "
                f"AI evaluation queued automatically for each."
            )
        if skipped_no_rubric:
            messages.error(request, f"No active rubric found for: {', '.join(skipped_no_rubric)} — skipped.")
        if skipped_existing:
            messages.error(request, f"Already assigned to {judge.username}: {', '.join(skipped_existing)} — skipped.")

        return redirect("dashboard:assign_submissions")

    submissions = (
        Submission.objects.filter(status=Submission.Status.SUBMITTED)
        .select_related("team", "competition", "submission_type")
        .prefetch_related("evaluations__judge")
    )

    existing_assignments = (
        Evaluation.objects.select_related("submission__team", "judge", "rubric")
        .order_by("-created_at")[:20]
    )

    judges = User.objects.filter(profile__role=UserProfile.Role.JUDGE)

    context = {
        "submissions": submissions,
        "existing_assignments": existing_assignments,
        "judges": judges,
        "active_tab": "assign",
    }
    return render(request, "dashboard/assign_submissions.html", context)