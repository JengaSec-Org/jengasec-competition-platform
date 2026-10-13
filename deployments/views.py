"""Team-facing submission, and the organiser screen behind it.

Routes a user may not use answer 404 rather than 403, as elsewhere on
the site, so they stay invisible. The machine endpoints the range calls
live in `deployments/ingest.py`.
"""
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from services import deployment_service as deploy
from services import team_service
from services.audit_service import record as audit
from services.submission_service import teams_for

from .forms import AppSubmissionForm
from .models import AppSubmission


def _team_for(user):
    """The Application Blue team this user is on, or None."""
    for team in teams_for(user).select_related("competition"):
        if deploy.submits_application(team):
            return team
    return None


def _visible(user, submission):
    if user.is_staff or user.is_superuser:
        return True
    return teams_for(user).filter(pk=submission.team_id).exists()


# ------------------------------------------------------------------- team


@login_required
def submit(request):
    """The form the briefing told blue captains to expect."""
    team = _team_for(request.user)
    if team is None:
        raise Http404("Not found")

    is_captain = team_service.is_captain_of(request.user, team)
    allowed, reason = deploy.can_submit(team)
    previous = deploy.latest_for_team(team)
    runtime = getattr(team, "app_runtime_status", None)

    form = None
    if is_captain and allowed:
        form = AppSubmissionForm(request.POST or None, previous=previous)
        if request.method == "POST" and form.is_valid():
            try:
                submission = deploy.submit(
                    team,
                    request.user,
                    image_reference=form.cleaned_data["image_reference"],
                    registry_username=form.cleaned_data["registry_username"],
                    pull_token=form.cleaned_data["pull_token"],
                    port=form.cleaned_data["port"],
                    health_path=form.cleaned_data["health_path"],
                    needs_database=form.cleaned_data["needs_database"],
                    env_vars=form.cleaned_data["env_text"],
                    extra_images=form.cleaned_data["extras_text"],
                )
            except (deploy.FreezePassed, deploy.NotEligible) as exc:
                messages.error(request, str(exc))
            else:
                audit(
                    AuditLog.Action.APP_SUBMITTED,
                    request=request,
                    target=submission,
                    description=f"Attempt {submission.attempt}: {submission.image_reference}",
                    cell=team.cell_label,
                )
                messages.success(
                    request,
                    f"Attempt {submission.attempt} recorded. "
                    f"The checks appear here as the range works through them.",
                )
                return redirect("deployments:submit")

    return render(request, "deployments/submit.html", {
        "team": team,
        "form": form,
        "is_captain": is_captain,
        "allowed": allowed,
        "reason": reason,
        "freeze_at": deploy.freeze_deadline(team.competition),
        "attempts": deploy.for_team(team)[:10],
        "latest": previous,
        "passing": deploy.last_passing(team),
        "runtime": runtime,
    })


@login_required
def detail(request, pk):
    submission = get_object_or_404(
        AppSubmission.objects.select_related("team", "competition"), pk=pk
    )
    if not _visible(request.user, submission):
        raise Http404("Not found")
    return render(request, "deployments/detail.html", {"submission": submission})


# -------------------------------------------------------------- organiser


@staff_member_required
def review(request, pk):
    """Set a check by hand, so the four states work with no range attached."""
    submission = get_object_or_404(
        AppSubmission.objects.select_related("team", "competition"), pk=pk
    )
    if request.method == "POST":
        status = request.POST.get("status", "")
        if status not in AppSubmission.Status.values:
            messages.error(request, "Unknown status.")
        else:
            deploy.record_status(
                submission, status, detail=request.POST.get("detail", "").strip()
            )
            audit(
                AuditLog.Action.APP_STATUS_CHANGED,
                request=request,
                target=submission,
                description=f"Set to {submission.get_status_display()}",
            )
            messages.success(request, f"Recorded as {submission.get_status_display()}.")
        return redirect("deployments:review", pk=submission.pk)

    return render(request, "deployments/review.html", {
        "submission": submission,
        "statuses": AppSubmission.Status.choices,
    })


@staff_member_required
def queue(request):
    """Every team's latest attempt, for the desk helping teams through it."""
    competition_id = request.GET.get("competition") or None
    rows = AppSubmission.objects.select_related("team", "competition").order_by(
        "team__enterprise", "team__cell_id", "-attempt"
    )
    if competition_id:
        rows = rows.filter(competition_id=competition_id)
    latest, seen = [], set()
    for row in rows:
        if row.team_id not in seen:
            seen.add(row.team_id)
            latest.append(row)
    competition = latest[0].competition if latest else None
    return render(request, "deployments/queue.html", {
        "rows": latest,
        "summary": deploy.summary(competition) if competition else None,
        "competition": competition,
    })


@staff_member_required
@require_POST
def reveal_token(request, pk):
    """Hand the registry token to an organiser, once, and record that.

    The range should normally read it from the machine endpoint rather
    than a person copying it, but on the day somebody will need to pull
    an image by hand, and the alternative is the token living in chat.
    """
    submission = get_object_or_404(AppSubmission, pk=pk)
    token = submission.reveal_pull_token()
    audit(
        AuditLog.Action.APP_TOKEN_REVEALED,
        request=request,
        target=submission,
        description=f"Registry token for attempt {submission.attempt} revealed",
    )
    if token is None:
        return HttpResponse(
            "No readable token is stored. Ask the team to submit it again.",
            content_type="text/plain", status=410,
        )
    # text/plain and no-store: it must not land in a template, a log or a cache.
    response = HttpResponse(token, content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response
