from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
.vem
from submissions.models import Submission
from services.notification_service import notify_user


@login_required
@user_passes_test(lambda u: u.is_staff or u.is_superuser or u.groups.filter(name="judge").exists())
def approve_score(request, submission_id):
    submission = get_object_or_404(Submission, pk=submission_id)

    if request.method == "POST":

        notify_user(
            submission.team,
            title="Your submission has been scored",
            message=f"Final score released for {submission.competition}",
            link="/dashboard/blue/",
            notification_type="submission_scored",
        )

        messages.success(request, f"Score approved for {submission.team}.")
        return redirect("judging:review_list")

    return render(request, "judging/review_detail.html", {"submission": submission})