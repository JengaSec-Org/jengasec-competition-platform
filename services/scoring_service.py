"""Turning marks into a standing.

Three things happen here, and only here:

* **Awarding** — a judge marks a finding valid, and the team's
  Application Red criteria are recomputed from every finding it has
  filed. Telemetry-derived marks and judges' own marks arrive the same
  way, through `award()`.
* **Totalling** — `recompute()` sums a team's events by stage, caps each
  stage at its published weight, and writes `TeamScore`.
* **Standing** — `leaderboard()` orders teams. Participants see the board
  as it was `LEADERBOARD_DELAY_MINUTES` ago; staff and judges see it now.

The delayed view is not a cached copy of the live one. It is the same
rebuild run with a cut-off, so what a participant sees is exactly what
the board really was half an hour ago -- including a mark that was
corrected since, which is the point: a team should not be able to infer
"we were just docked" from a number that moved before anyone told them.

The scheme -- stages, weights, criteria, and how findings become marks --
is `competitions.grading`. This module decides nothing about what a mark
is worth.
"""
import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from competitions import grading

logger = logging.getLogger(__name__)

# §20 — participants get a delayed view; admins see real time.
from services.competition_service import LEADERBOARD_DELAY_MINUTES  # noqa: E402

ZERO = Decimal("0")


# ------------------------------------------------------------------ awarding


def award(team, stage, marks, *, source, criterion="", note="", day=None,
          finding=None, recorded_by=None, dedupe_key=""):
    """Record one mark. Returns the `ScoreEvent`.

    With a `dedupe_key` the mark is updated in place rather than added
    again, so a computation can be re-run and a judge can change a
    finding's severity without the team being paid twice.
    """
    from scoring.models import ScoreEvent

    values = {
        "stage": stage,
        "criterion": criterion,
        "marks": Decimal(str(marks)),
        "source": source,
        "note": note,
        "day": day,
        "finding": finding,
        "recorded_by": recorded_by,
    }
    if dedupe_key:
        event, _ = ScoreEvent.objects.update_or_create(
            competition=team.competition, team=team, dedupe_key=dedupe_key,
            defaults=values,
        )
        return event
    return ScoreEvent.objects.create(
        competition=team.competition, team=team, dedupe_key="", **values
    )


@transaction.atomic
def award_for_findings(team, *, recorded_by=None):
    """Recompute an Application Red team's main-competition marks.

    Every criterion is recalculated from every finding the team has
    filed, not adjusted by the one that changed: a judge downgrading a
    severity or marking something a duplicate has to be able to take
    marks away, and an incremental award cannot do that.
    """
    from cyberrange.models import Finding

    if grading.side_key(team) != "application_red":
        return []

    findings = list(
        Finding.objects.filter(red_team=team, competition=team.competition)
        .prefetch_related("duplicates")
    )
    marks = grading.finding_marks(findings)
    labels = {key: label for key, label, _, _ in grading.MAIN_BY_SIDE["application_red"]}

    events = []
    for criterion, value in marks.items():
        events.append(award(
            team, "main", value,
            source="finding",
            criterion=criterion,
            note=f"{labels.get(criterion, criterion)}, from {len(findings)} finding(s).",
            recorded_by=recorded_by,
            dedupe_key=f"findings:{criterion}",
        ))
    recompute(team)
    return events


def award_for_finding(finding, *, recorded_by=None):
    """Called when a judge reviews a finding. Scores the whole team."""
    return award_for_findings(finding.red_team, recorded_by=recorded_by)


def award_for_proposal(evaluation, *, recorded_by=None):
    """Carry an approved proposal evaluation into the team's Entry mark.

    `Evaluation.recalculate_total()` returns a weighted total on the
    rubric's own scale — 150 for the Entry Guide's weights, not 100 — and
    has already applied any penalties recorded on the submission. Dividing
    by the rubric's total weight gives a mark out of 100; the Entry stage
    takes 30 of it.

    Only called when a judge approves. The model's own numbers never
    become a mark on their own.
    """
    from competitions import grading

    rubric = evaluation.rubric
    scale = rubric.total_weight()
    if not scale:
        logger.warning("Rubric %s has no weight; no Entry mark awarded.", rubric.pk)
        return None

    total = evaluation.weighted_total
    if total is None:
        total = evaluation.recalculate_total()

    out_of_100 = Decimal(str(total)) / Decimal(str(scale)) * Decimal("100")
    stage_weight = next(w for name, w in grading.SCORE_WEIGHTS if name == "Entry")
    marks = (out_of_100 * Decimal(str(stage_weight)) / Decimal("100")).quantize(
        Decimal("0.01")
    )

    team = evaluation.submission.team
    event = award(
        team, "entry", marks,
        source="entry",
        note=(
            f"{evaluation.submission.submission_type.name}: "
            f"{out_of_100.quantize(Decimal('0.1'))}/100, approved by a judge."
        ),
        recorded_by=recorded_by,
        # One Entry mark per team, replaced if the evaluation is revised.
        dedupe_key="proposal",
    )
    recompute(team)
    return event


# ------------------------------------------------------------------ totalling


def stage_totals(team, *, as_of=None):
    """{stage: marks} for one team, each capped at its published weight."""
    from scoring.models import ScoreEvent

    events = ScoreEvent.objects.filter(competition=team.competition, team=team)
    if as_of is not None:
        events = events.filter(recorded_at__lte=as_of)

    raw = {
        row["stage"]: row["total"] or ZERO
        for row in events.values("stage").annotate(total=Sum("marks"))
    }
    totals = {}
    for stage in grading.STAGES:
        cap = Decimal(str(stage["weight"]))
        value = Decimal(str(raw.get(stage["key"], ZERO)))
        # Capped at the stage weight and floored at zero: a conduct penalty
        # takes marks off that stage, never off the team's other work.
        totals[stage["key"]] = max(min(value, cap), ZERO)
    return totals


def recompute(team, *, as_of=None, save=True):
    """Rebuild and store this team's total. Returns the `TeamScore`."""
    from scoring.models import TeamScore

    totals = stage_totals(team, as_of=as_of)
    total = sum(totals.values(), ZERO)
    if not save:
        return TeamScore(
            competition=team.competition, team=team, total=total,
            entry=totals["entry"], main=totals["main"],
            documentation=totals["documentation"],
        )
    score, _ = TeamScore.objects.update_or_create(
        competition=team.competition, team=team,
        defaults={
            "entry": totals["entry"],
            "main": totals["main"],
            "documentation": totals["documentation"],
            "total": total,
        },
    )
    return score


def recompute_all(competition):
    """Rebuild every team's total. The repair tool when something drifts."""
    from accounts.models import Team

    teams = Team.objects.filter(competition=competition).exclude(
        status=Team.Status.WITHDRAWN
    )
    return [recompute(team) for team in teams]


# ----------------------------------------------------------------- standing


def leaderboard(competition, *, for_staff=False, as_of=None, limit=None):
    """The standing, newest first by total.

    Staff and judges see it live. Everyone else sees it as it stood
    `LEADERBOARD_DELAY_MINUTES` ago, rebuilt from events rather than
    cached, so the delay is real rather than cosmetic.
    """
    from accounts.models import Team

    teams = list(
        Team.objects.filter(competition=competition)
        .exclude(status=Team.Status.WITHDRAWN)
        .select_related("competition")
    )

    if for_staff and as_of is None:
        from scoring.models import TeamScore

        stored = {
            score.team_id: score
            for score in TeamScore.objects.filter(competition=competition)
        }
        rows = [
            {"team": team, "score": stored.get(team.pk) or recompute(team, save=False)}
            for team in teams
        ]
    else:
        cutoff = as_of or (
            timezone.now() - timezone.timedelta(minutes=LEADERBOARD_DELAY_MINUTES)
        )
        rows = [
            {"team": team, "score": recompute(team, as_of=cutoff, save=False)}
            for team in teams
        ]

    rows.sort(key=lambda row: (-row["score"].total, row["team"].team_name.lower()))

    # Shared rank for a shared total, and the next rank skips -- an event
    # where two teams tie for second has no third.
    place, previous = 0, None
    for index, row in enumerate(rows, start=1):
        if row["score"].total != previous:
            place, previous = index, row["score"].total
        row["rank"] = place
    return rows[:limit] if limit else rows


def feed(competition, *, for_staff=False, limit=60, since=None):
    """Marks as they land: the venue scoreboard's running commentary.

    Staff and judges see every mark the moment it is recorded. Everyone
    else sees marks older than `LEADERBOARD_DELAY_MINUTES`, so a red team
    cannot watch the board to work out, in real time, which of its
    findings a judge has just accepted.

    Ordered newest first and capped, because this is read on a projector
    by a room, not paged through.
    """
    from scoring.models import ScoreEvent

    events = (
        ScoreEvent.objects.filter(competition=competition)
        .select_related("team", "finding", "recorded_by")
    )
    if not for_staff:
        events = events.filter(
            recorded_at__lte=timezone.now()
            - timezone.timedelta(minutes=LEADERBOARD_DELAY_MINUTES)
        )
    if since is not None:
        events = events.filter(recorded_at__gt=since)
    return list(events[:limit])


def cutoff_for(for_staff):
    """The moment a viewer's board is showing, or None when it is live."""
    if for_staff:
        return None
    return timezone.now() - timezone.timedelta(minutes=LEADERBOARD_DELAY_MINUTES)


def breakdown(team, *, as_of=None):
    """Every mark behind a team's total, for the team and for a judge."""
    from scoring.models import ScoreEvent

    events = ScoreEvent.objects.filter(competition=team.competition, team=team)
    if as_of is not None:
        events = events.filter(recorded_at__lte=as_of)
    by_stage = {stage["key"]: [] for stage in grading.STAGES}
    for event in events.select_related("finding").order_by("stage", "criterion"):
        by_stage.setdefault(event.stage, []).append(event)

    totals = stage_totals(team, as_of=as_of)
    out = []
    for stage in grading.stages(team):
        out.append({
            **stage,
            "events": by_stage.get(stage["key"], []),
            "awarded": totals.get(stage["key"], ZERO),
        })
    return out


# -------------------------------------------------- the old module interface


def weighted_score(criterion_scores):
    """Aggregate per-criterion scores into a weighted total.

    Each entry: {"score": float, "max": float, "weight": float}, weights
    summing to 1.0. Kept because `judging` was written against it; an
    entry with no `max` contributes nothing rather than dividing by zero.
    """
    total = 0.0
    for row in criterion_scores:
        maximum = float(row.get("max") or 0)
        if maximum <= 0:
            continue
        total += (float(row.get("score") or 0) / maximum) * float(row.get("weight") or 0)
    return round(total * 100, 2)


def final_team_score(component_scores, component_weights):
    """Combine stage scores using the published weights.

    Both arguments are keyed by stage. A stage with no weight is ignored
    rather than assumed, so a half-configured scheme cannot quietly
    inflate a mark.
    """
    return round(
        sum(
            float(component_scores.get(key, 0)) * float(weight)
            for key, weight in component_weights.items()
        ),
        2,
    )
