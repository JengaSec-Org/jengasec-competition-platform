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
the board really was five minutes ago -- including a mark that was
corrected since, which is the point: a team should not be able to infer
"we were just docked" from a number that moved before anyone told them.

The scheme -- stages, weights, criteria, and how findings become marks --
is `competitions.grading`. This module decides nothing about what a mark
is worth.
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from competitions import grading
from jengasec_scoring_policy import number

logger = logging.getLogger(__name__)

# §20 — participants get a delayed view; admins see real time.
from services.competition_service import LEADERBOARD_DELAY_MINUTES  # noqa: E402

ZERO = Decimal("0")


# ------------------------------------------------------------------ awarding


class OverrideHeld(ValueError):
    pass


def _append_revision(event, *, metadata=None):
    from scoring.models import ScoreRevision

    latest = event.revisions.order_by("-sequence").first()
    fields = {key: getattr(event, key) for key in (
        "stage", "criterion", "marks", "day", "source", "note", "finding",
        "recorded_by", "override_active")}
    fields.update(metadata or {})
    return ScoreRevision.objects.create(
        event=event, sequence=latest.sequence + 1 if latest else 1,
        accepted_at=timezone.now(), **fields
    )


@transaction.atomic
def award(team, stage, marks, *, source, criterion="", note="", day=None,
          finding=None, recorded_by=None, dedupe_key="", metadata=None,
          override_active=None):
    """Record one mark. Returns the `ScoreEvent`.

    With a `dedupe_key` the mark is updated in place rather than added
    again, so a computation can be re-run and a judge can change a
    finding's severity without the team being paid twice.
    """
    from accounts.models import Team
    from scoring.models import ScoreEvent, ScoringPolicyEnrollment

    # The team lock also serializes the first insert for a component. A row
    # lock on a not-yet-existing ScoreEvent alone cannot do that.
    Team.objects.select_for_update().get(pk=team.pk)
    if stage not in {s["key"] for s in grading.STAGES} | {grading.BONUS_STAGE}:
        raise ValueError("unknown score stage")
    if source not in ScoreEvent.Source.values:
        raise ValueError("unknown score source")
    if not isinstance(criterion, str) or len(criterion) > 40:
        raise ValueError("invalid criterion")
    if not isinstance(dedupe_key, str) or len(dedupe_key) > 120:
        raise ValueError("invalid component key")
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError("note is too long")
    if day is not None and (type(day) is not int or day not in (1, 2, 3)):
        raise ValueError("day must be 1, 2 or 3")
    value = number(marks, "marks").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(value) > Decimal("9999.99"):
        raise ValueError("marks exceed storage bounds")
    caps = {key: Decimal(str(cap)) for key, _, cap, _ in grading.rows_for(stage, team)}
    if criterion and criterion not in caps:
        raise ValueError("criterion does not belong to this team and stage")
    if source == "telemetry" and (value < 0 or (
        stage == "main" and grading.side_key(team) == "application_red"
    )):
        raise ValueError("telemetry cannot award this mark")
    if criterion:
        value = min(value, caps[criterion])
    enrolled = ScoringPolicyEnrollment.objects.filter(competition=team.competition).exists()
    if enrolled and stage == "main" and criterion:
        dedupe_key = f"main:{criterion}"
    if override_active is None:
        override_active = source == "judge" and bool(dedupe_key)

    values = {
        "stage": stage,
        "criterion": criterion,
        "marks": value,
        "source": source,
        "note": note,
        "day": day,
        "finding": finding,
        "recorded_by": recorded_by,
        "override_active": override_active,
    }
    if dedupe_key:
        event = ScoreEvent.objects.select_for_update().filter(
            competition=team.competition, team=team, dedupe_key=dedupe_key
        ).first()
        if event is not None:
            if event.override_active and source != "judge":
                raise OverrideHeld("judge override is active")
            for key, val in values.items():
                setattr(event, key, val)
            event.save()
        else:
            event = ScoreEvent.objects.create(
                competition=team.competition, team=team, dedupe_key=dedupe_key, **values)
    else:
        event = ScoreEvent.objects.create(
            competition=team.competition, team=team, dedupe_key="", **values)
    _append_revision(event, metadata=metadata)
    return event


@transaction.atomic
def release_override(event, *, recorded_by, note):
    """Explicitly restore the last automatic revision without losing history."""
    from accounts.models import Team
    from scoring.models import ScoreEvent

    if not note.strip():
        raise ValueError("an override release needs a reason")
    Team.objects.select_for_update().get(pk=event.team_id)
    event = ScoreEvent.objects.select_for_update().get(pk=event.pk)
    if not event.override_active:
        return event
    previous = event.revisions.filter(override_active=False).exclude(source="judge").order_by("-sequence").first()
    metadata = {}
    if previous:
        for key in ("stage", "criterion", "marks", "day", "source", "finding"):
            setattr(event, key, getattr(previous, key))
        metadata = {key: getattr(previous, key) for key in (
            "policy_version", "policy_sha256", "window_start", "window_end", "facts",
            "quality", "assignment_revisions", "evidence", "input_sha256")}
    else:
        metadata["quality"] = {"pending_reasons": ["awaiting automatic calculation after release"]}
    event.override_active = False
    event.note = "Override released: " + note.strip()[:1900]
    event.recorded_by = recorded_by
    event.save()
    _append_revision(event, metadata=metadata)
    recompute(event.team)
    return event


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
            metadata={"facts": {"findings": [{"id": f.pk, "status": f.status,
                "severity": f.severity, "duplicate_of": f.duplicate_of_id,
                "target_team": f.target_team_id, "updated_at": f.updated_at.isoformat()}
                for f in findings]}},
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


def visible_events(competition, *, team=None, as_of=None):
    """The accepted revision of each logical component at a point in time."""
    from scoring.models import ScoreEvent, ScoreRevision

    latest = ScoreRevision.objects.filter(event_id=OuterRef("pk"))
    if as_of is not None:
        latest = latest.filter(accepted_at__lte=as_of)
    components = ScoreEvent.objects.filter(competition=competition)
    if team is not None:
        components = components.filter(team=team)
    components = components.annotate(
        visible_revision=Subquery(latest.order_by("-sequence").values("pk")[:1]))
    return ScoreRevision.objects.filter(
        pk__in=Subquery(components.values("visible_revision"))
    ).select_related("event__team", "finding", "recorded_by")


def stage_totals(team, *, as_of=None):
    """{stage: marks} for one team, each capped at its published weight."""
    events = visible_events(team.competition, team=team, as_of=as_of)
    components = {}
    overrides = {}
    for event in events:
        key = (event.stage, event.criterion)
        components[key] = components.get(key, ZERO) + event.marks
        if event.override_active and key not in overrides:
            overrides[key] = event.marks
    components.update(overrides)
    raw = {}
    for (stage, criterion), value in components.items():
        caps = {key: Decimal(str(cap)) for key, _, cap, _ in grading.rows_for(stage, team)}
        if criterion in caps:
            value = min(value, caps[criterion])
        raw[stage] = raw.get(stage, ZERO) + value
    totals = {}
    for stage in grading.STAGES:
        cap = Decimal(str(stage["weight"]))
        value = Decimal(str(raw.get(stage["key"], ZERO)))
        # Capped at the stage weight and floored at zero: a conduct penalty
        # takes marks off that stage, never off the team's other work.
        totals[stage["key"]] = max(min(value, cap), ZERO)
    # Bonus sits outside the hundred and is deliberately not capped, so a
    # team that finished far ahead of the qualifying mark keeps the excess.
    totals[grading.BONUS_STAGE] = max(
        Decimal(str(raw.get(grading.BONUS_STAGE, ZERO))), ZERO
    )
    return totals


def recompute(team, *, as_of=None, save=True):
    """Rebuild and store this team's total. Returns the `TeamScore`."""
    from scoring.models import ScoreRevision, TeamScore

    totals = stage_totals(team, as_of=as_of)
    total = sum(totals.values(), ZERO)
    fields = {
        "entry": totals["entry"],
        "main": totals["main"],
        "documentation": totals["documentation"],
        "bonus": totals[grading.BONUS_STAGE],
        "total": total,
    }
    if not save or as_of is not None:
        score = TeamScore(competition=team.competition, team=team, **fields)
        score.history_available = as_of is None or not ScoreRevision.objects.filter(
            event__team=team, sequence=1, baseline=True, accepted_at__gt=as_of,
            event__recorded_at__lte=as_of).exists()
        return score
    score, _ = TeamScore.objects.update_or_create(
        competition=team.competition, team=team, defaults=fields,
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

    for row in rows:
        row["history_available"] = getattr(row["score"], "history_available", True)
    rows.sort(key=lambda row: (not row["history_available"], -row["score"].total, row["team"].team_name.lower()))

    # Shared rank for a shared total, and the next rank skips -- an event
    # where two teams tie for second has no third.
    place, previous = 0, None
    for index, row in enumerate(rows, start=1):
        if not row["history_available"]:
            row["rank"] = None
            continue
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
    events = visible_events(competition, as_of=cutoff_for(for_staff))
    if since is not None:
        events = events.filter(accepted_at__gt=since)
    return list(events[:limit])


def cutoff_for(for_staff):
    """The moment a viewer's board is showing, or None when it is live."""
    if for_staff:
        return None
    return timezone.now() - timezone.timedelta(minutes=LEADERBOARD_DELAY_MINUTES)


def breakdown(team, *, as_of=None):
    """Every mark behind a team's total, for the team and for a judge."""
    events = visible_events(team.competition, team=team, as_of=as_of)
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
