"""Competition Management (Module 2).

Runs the competition lifecycle without dropping to Django admin: create
competitions, define timelines, and open or close submissions and
judging. Organisers only — every view is staff-gated and every state
change is a CSRF-protected POST.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from audit.models import AuditLog
from services.audit_service import record as audit

from .forms import CompetitionForm, CompetitionSettingsForm
from .models import Competition


def _require_staff(user):
    """Competition management is organiser-only.

    404 rather than 403 so the URLs don't confirm they exist to a
    participant poking at the routes.
    """
    if not (user.is_active and (user.is_staff or user.is_superuser)):
        raise Http404("Not found")


@login_required
def competition_list(request):
    _require_staff(request.user)
    competitions = Competition.objects.annotate(
        team_count=Count("teams", distinct=True),
        submission_count=Count("submissions", distinct=True),
    )
    return render(
        request,
        "competitions/competition_list.html",
        {"competitions": competitions},
    )


@login_required
def competition_detail(request, pk):
    _require_staff(request.user)
    competition = get_object_or_404(
        Competition.objects.prefetch_related("teams", "application_briefs", "phases"),
        pk=pk,
    )
    return render(
        request,
        "competitions/competition_detail.html",
        {
            "competition": competition,
            "settings": competition.settings,
            "settings_form": CompetitionSettingsForm(instance=competition.settings),
            "phases": Competition.Phase.choices,
            "teams": competition.teams.all(),
            "briefs": competition.application_briefs.all(),
        },
    )


@login_required
def competition_create(request):
    _require_staff(request.user)
    form = CompetitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        competition = form.save(commit=False)
        competition.created_by = request.user
        competition.save()
        competition.settings  # materialise the settings row
        audit(
            AuditLog.Action.COMPETITION_CREATED,
            request=request,
            target=competition,
            description=f"Created {competition.name}",
        )
        messages.success(request, f"{competition.name} created.")
        return redirect("competitions:detail", pk=competition.pk)
    return render(
        request, "competitions/competition_form.html", {"form": form}
    )


@login_required
def competition_update(request, pk):
    _require_staff(request.user)
    competition = get_object_or_404(Competition, pk=pk)
    form = CompetitionForm(request.POST or None, instance=competition)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(
            AuditLog.Action.COMPETITION_UPDATED,
            request=request,
            target=competition,
            description="Competition details updated",
        )
        messages.success(request, "Competition updated.")
        return redirect("competitions:detail", pk=competition.pk)
    return render(
        request,
        "competitions/competition_form.html",
        {"form": form, "competition": competition},
    )


@login_required
def competition_settings(request, pk):
    """Timelines and caps."""
    _require_staff(request.user)
    competition = get_object_or_404(Competition, pk=pk)
    form = CompetitionSettingsForm(
        request.POST or None, instance=competition.settings
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(
            AuditLog.Action.COMPETITION_UPDATED,
            request=request,
            target=competition,
            description="Timelines and caps updated",
        )
        messages.success(request, "Timelines updated.")
        return redirect("competitions:detail", pk=competition.pk)
    return render(
        request,
        "competitions/competition_settings.html",
        {"form": form, "competition": competition},
    )


# Actions the lifecycle panel can perform, kept as an explicit allow-list
# so a posted action name can never reach arbitrary attribute setting.
LIFECYCLE_ACTIONS = {
    "open_submissions": ("submissions_open", True, "Submissions are open."),
    "close_submissions": ("submissions_open", False, "Submissions are closed."),
    "open_judging": ("judging_open", True, "Judging is open."),
    "close_judging": ("judging_open", False, "Judging is closed."),
    "publish_results": ("results_published", True, "Results published to teams."),
    "unpublish_results": ("results_published", False, "Results hidden from teams."),
}


@login_required
def competition_lifecycle(request, pk):
    """Apply one lifecycle change. POST only."""
    _require_staff(request.user)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    competition = get_object_or_404(Competition, pk=pk)
    action = request.POST.get("action", "")

    if action == "set_phase":
        phase = request.POST.get("phase", "")
        if phase not in Competition.Phase.values:
            messages.error(request, "Unknown phase.")
        else:
            competition.phase = phase
            competition.save(update_fields=["phase", "updated_at"])
            audit(
                AuditLog.Action.LIFECYCLE_CHANGED,
                request=request,
                target=competition,
                description=f"Phase -> {Competition.Phase(phase).label}",
            )
            messages.success(
                request,
                f"Phase set to {Competition.Phase(phase).label}.",
            )
    elif action == "set_status":
        status = request.POST.get("status", "")
        if status not in Competition.Status.values:
            messages.error(request, "Unknown status.")
        else:
            competition.status = status
            competition.save(update_fields=["status", "updated_at"])
            audit(
                AuditLog.Action.LIFECYCLE_CHANGED,
                request=request,
                target=competition,
                description=f"Status -> {Competition.Status(status).label}",
            )
            messages.success(
                request,
                f"Status set to {Competition.Status(status).label}.",
            )
    elif action in LIFECYCLE_ACTIONS:
        field, value, note = LIFECYCLE_ACTIONS[action]
        settings_row = competition.settings
        setattr(settings_row, field, value)
        settings_row.save(update_fields=[field, "updated_at"])
        audit(
            AuditLog.Action.LIFECYCLE_CHANGED,
            request=request,
            target=competition,
            description=note,
            field=field,
            value=value,
        )
        messages.success(request, note)
    else:
        messages.error(request, "Unknown action.")

    return redirect("competitions:detail", pk=competition.pk)
