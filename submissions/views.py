from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

from .models import Submission
from .forms import SubmissionForm
from services.notification_service import notify_admins, notify_judges


@login_required
def upload_submission(request):
    if request.method == "POST":
        form = SubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.team = request.user
            submission.save()

            notify_admins(
                title="New submission received",
                message=f"{submission.team} submitted for {submission.competition}",
                link="/dashboard/command/",
                notification_type="submission_received",
            )
            notify_judges(
                title="New submission awaiting review",
                message=f"{submission.team} submitted for {submission.competition}",
                link="/dashboard/insights/",
                notification_type="submission_received",
            )

            messages.success(request, "Submission uploaded successfully.")
            return redirect("submissions:submission_list")
    else:
        form = SubmissionForm()

    return render(request, "submissions/upload.html", {"form": form})


@login_required
def submission_list(request):
    submissions = Submission.objects.filter(team=request.user)
    return render(request, "submissions/list.html", {"submissions": submissions})