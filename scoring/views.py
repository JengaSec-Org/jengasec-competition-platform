"""The board, and the screen a judge marks on.

Who sees what:

* **Participants** see the board as it stood `LEADERBOARD_DELAY_MINUTES`
  ago, and their own breakdown in full. A team may always see every mark
  against itself -- an appeal it cannot read is not an appeal.
* **Judges and staff** see the board live and mark on it.

Routes a user may not use answer 404, as elsewhere on the site.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Team
from accounts.roles import JUDGE, user_in_role
from audit.models import AuditLog
from competitions import grading
from competitions.models import Competition
from services import scoring_service
from services.audit_service import record as audit
from services.submission_service import teams_for
from jengasec_scoring_policy import FORMULAS
from scoring.models import ScoreEvent, ScoreRevision, ScoringEvidence, ScoringReadiness
from scoring.protocol import ProtocolError
from services import appeal_service
from judging.forms import AppealForm
from judging.models import Appeal


def _is_marker(user):
    return user.is_active and (
        user.is_staff or user.is_superuser or user_in_role(user, JUDGE)
    )


marker_required = user_passes_test(_is_marker)


def _competition_for(user):
    team = teams_for(user).select_related("competition").first()
    if team is not None:
        return team.competition
    return (
        Competition.objects.filter(
            status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS)
        )
        .order_by("-start_date")
        .first()
    )


@login_required
def leaderboard(request):
    """The standing. Live for markers, delayed for everyone else."""
    competition = _competition_for(request.user)
    if competition is None:
        raise Http404("Not found")

    live = _is_marker(request.user)
    rows = scoring_service.leaderboard(competition, for_staff=live)
    mine = teams_for(request.user).values_list("pk", flat=True)

    return render(request, "scoring/leaderboard.html", {
        "competition": competition,
        "rows": rows,
        "live": live,
        "delay_minutes": scoring_service.LEADERBOARD_DELAY_MINUTES,
        "my_team_ids": set(mine),
        "stages": grading.STAGES,
    })


@login_required
@marker_required
def scheme(request):
    """The whole marking scheme, for the people applying it.

    Teams see their own side on the Info page. A judge marks across all
    four sides and against the proposal rubrics, so they need the lot on
    one page — including the sections a proposal is marked on, which until
    now existed only in the Entry Guide.
    """
    from competitions import proposal_rubric
    from rubrics.models import Rubric

    competition = _competition_for(request.user)
    sides = [
        ("Application Blue", "application_blue"),
        ("Application Red", "application_red"),
        ("AI Defence Blue", "ai_blue"),
        ("AI Red", "ai_red"),
    ]
    main = next(s for s in grading.STAGES if s["key"] == "main")
    return render(request, "scoring/scheme.html", {
        "competition": competition,
        "stages": grading.STAGES,
        "main_weight": main["weight"],
        "sides": [
            {
                "name": name,
                "rows": [
                    {"label": label, "marks": marks, "note": note}
                    for _, label, marks, note in grading.MAIN_BY_SIDE[key]
                ],
            }
            for name, key in sides
        ],
        "documentation": [
            {"label": label, "marks": marks, "note": note}
            for _, label, marks, note in grading.DOCUMENTATION
        ],
        "rubrics": Rubric.objects.filter(
            competition=competition
        ).prefetch_related("criteria").order_by("submission_type__name")
        if competition else [],
        "penalties": proposal_rubric.PENALTIES,
        "finding_marks": grading.FINDING_MARKS,
    })


@login_required
def live(request):
    """The venue scoreboard: the standing, and marks as they land.

    Built to be left on a projector, so it refreshes itself and says
    plainly whose clock it is showing. Everything a participant sees here
    is at least `LEADERBOARD_DELAY_MINUTES` old; markers see it live.
    """
    competition = _competition_for(request.user)
    if competition is None:
        raise Http404("Not found")

    live_view = _is_marker(request.user)
    return render(request, "scoring/live.html", {
        "competition": competition,
        "rows": scoring_service.leaderboard(competition, for_staff=live_view, limit=12),
        "events": scoring_service.feed(competition, for_staff=live_view, limit=40),
        "live": live_view,
        "delay_minutes": scoring_service.LEADERBOARD_DELAY_MINUTES,
        "showing": scoring_service.cutoff_for(live_view),
        "my_team_ids": set(teams_for(request.user).values_list("pk", flat=True)),
        "stages": grading.STAGES,
        # Seconds between refreshes. Short enough to feel live, long enough
        # that a room full of open tabs is not a load test.
        "refresh_seconds": 30,
    })


@login_required
def team_breakdown(request, pk):
    """Every mark behind one team's total."""
    team = get_object_or_404(Team.objects.select_related("competition"), pk=pk)
    if not (_is_marker(request.user) or teams_for(request.user).filter(pk=pk).exists()):
        raise Http404("Not found")

    return render(request, "scoring/breakdown.html", {
        "team": team,
        "stages": scoring_service.breakdown(team),
        "score": scoring_service.recompute(team, save=False),
        "can_mark": _is_marker(request.user),
        "readiness": ScoringReadiness.objects.filter(team=team).order_by("criterion"),
    })


@login_required
@marker_required
def mark(request, pk):
    """Enter a judge's marks for one team, criterion by criterion.

    Application Red's main-competition marks are computed from findings
    and are shown read-only here: typing over them would be silently
    undone the next time a finding is reviewed.
    """
    team = get_object_or_404(Team.objects.select_related("competition"), pk=pk)
    computed = grading.side_key(team) == "application_red"

    if request.method == "POST":
        saved, errors = 0, []
        for stage in grading.stages(team):
            rows = stage["rows"] or [{"key": "", "label": stage["name"],
                                      "marks": stage["weight"], "note": ""}]
            for row in rows:
                if stage["key"] == "main" and computed:
                    continue  # findings own these
                field = f"{stage['key']}:{row['key']}"
                raw = (request.POST.get(field) or "").strip()
                if raw == "":
                    continue
                try:
                    value = Decimal(raw)
                except (InvalidOperation, ValueError):
                    errors.append(f"{row['label']}: '{raw}' is not a number.")
                    continue
                if not value.is_finite() or value < 0 or value > Decimal(str(row["marks"])):
                    errors.append(
                        f"{row['label']}: {value} is outside 0–{row['marks']}."
                    )
                    continue
                scoring_service.award(
                    team, stage["key"], value,
                    source="entry" if stage["key"] == "entry" else "judge",
                    criterion=row["key"],
                    note=(request.POST.get(f"note:{field}") or "").strip(),
                    recorded_by=request.user,
                    dedupe_key=f"judge:{stage['key']}:{row['key']}",
                )
                saved += 1

        for error in errors:
            messages.error(request, error)
        if saved:
            score = scoring_service.recompute(team)
            audit(
                AuditLog.Action.SCORE_OVERRIDDEN,
                request=request,
                target=team,
                description=f"{saved} mark(s) recorded; total now {score.total}/100",
            )
            messages.success(request, f"{saved} mark(s) recorded. Total {score.total}/100.")
        return redirect("scoring:mark", pk=team.pk)

    # Each row carries its own current mark, because a template cannot look
    # a dictionary up by a key it has to build first.
    existing = {}
    for stage in scoring_service.breakdown(team):
        for event in stage["events"]:
            existing[(event.stage, event.criterion)] = event

    stages = []
    for stage in grading.stages(team):
        rows = []
        for row in stage["rows"]:
            event = existing.get((stage["key"], row["key"]))
            rows.append({**row, "current": event.marks if event else "",
                         "current_note": event.note if event else ""})
        carried = existing.get((stage["key"], ""))
        stages.append({
            **stage,
            "rows": rows,
            "current": carried.marks if carried else "",
            "current_note": carried.note if carried else "",
        })

    return render(request, "scoring/mark.html", {
        "team": team,
        "stages": stages,
        "computed_main": computed,
        "score": scoring_service.recompute(team, save=False),
    })


@login_required
def revision_detail(request, pk):
    revision = get_object_or_404(ScoreRevision.objects.select_related("event__team"), pk=pk)
    team = revision.team
    staff = _is_marker(request.user)
    if not (staff or teams_for(request.user).filter(pk=team.pk).exists()):
        raise Http404("Not found")
    return render(request, "scoring/revision.html", _revision_context(revision, request.user))


def _revision_context(revision, user, *, form=None):
    team = revision.team
    appeals = Appeal.objects.filter(score_event=revision.event).select_related("score_revision", "second_judge", "resolved_by")
    active = appeals.filter(status__in=appeal_service.ACTIVE_STATUSES).exists()
    appeals = list(appeals)
    for appeal in appeals:
        appeal.can_review = _is_marker(user) and (user.is_staff or user.is_superuser or appeal.second_judge_id == user.pk)
    is_open, closes = appeal_service.appeal_window(revision)
    return {
        "revision": revision, "team": team, "can_mark": _is_marker(user),
        "formula": FORMULAS.get(grading.side_key(team), {}).get(revision.criterion, ""),
        "history": revision.event.revisions.order_by("-sequence"),
        "facts": revision.facts.items(), "quality": revision.quality.items(),
        "appeals": appeals, "appeal_form": form or AppealForm(), "appeal_closes_at": closes,
        "can_file_appeal": is_open and user.pk == team.captain_id and not active,
    }


@login_required
def file_score_appeal(request, pk):
    revision = get_object_or_404(ScoreRevision.objects.select_related("event__team"), pk=pk)
    if not teams_for(request.user).filter(pk=revision.team_id).exists():
        raise Http404("Not found")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from judging.appeal_views import _bounded
    if not _bounded(request):
        return HttpResponse("Appeal request is too large.", status=413)
    form = AppealForm(request.POST)
    if form.is_valid():
        try:
            appeal_service.file_score_appeal(revision, request.user,
                                             form.cleaned_data["grounds"], form.cleaned_data["reason"])
        except appeal_service.AppealError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Appeal filed against this score revision.")
            return redirect("scoring:revision", pk=revision.pk)
    return render(request, "scoring/revision.html", _revision_context(revision, request.user, form=form), status=400)


@login_required
def download_evidence(request, pk):
    # Archives can contain staff-only traffic, query results and ground truth.
    # Teams receive the facts and explanation through their score revision.
    if not _is_marker(request.user):
        raise Http404("Not found")
    evidence = get_object_or_404(ScoringEvidence, pk=pk)
    from services.scoring_evidence import verified_bytes

    try:
        data = verified_bytes(evidence)
    except ProtocolError:
        return HttpResponse("Evidence is temporarily unavailable.", status=503, content_type="text/plain")
    response = HttpResponse(data, content_type="application/json")
    response["Content-Disposition"] = f'attachment; filename="evidence-{evidence.pk}.json"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@marker_required
def release_override(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event = get_object_or_404(ScoreEvent, pk=pk)
    note = request.POST.get("note", "")
    try:
        scoring_service.release_override(event, recorded_by=request.user, note=note)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        audit(AuditLog.Action.SCORE_OVERRIDDEN, request=request, target=event.team,
              description=f"Released {event.stage}/{event.criterion} override: {note[:200]}")
        messages.success(request, "Automatic updates are enabled for this criterion.")
    return redirect("scoring:breakdown", pk=event.team_id)
