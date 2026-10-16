"""Explicit policy enrollment, baseline reconciliation and finalization."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import Team
from competitions import grading
from jengasec_scoring_policy import POLICY_SHA256, POLICY_VERSION, digest
from scoring.models import ScoreEvent, ScoringPolicyEnrollment
from services import scoring_service


def reconciliation_report(competition):
    rows, issues = [], []
    for team in Team.objects.filter(competition=competition).order_by("pk"):
        events = list(ScoreEvent.objects.filter(team=team).order_by("pk"))
        old = {}
        for event in events:
            old[event.stage] = old.get(event.stage, Decimal(0)) + event.marks
            if event.stage == "main" and event.criterion not in {
                key for key, _, _, _ in grading.rows_for("main", team)
            }:
                issues.append(f"team {team.pk} has a Main mark without a recognized criterion")
        before = {s["key"]: str(max(Decimal(0), min(old.get(s["key"], Decimal(0)), s["weight"])))
                  for s in grading.STAGES}
        before["bonus"] = str(max(Decimal(0), old.get("bonus", Decimal(0))))
        after = {key: str(value) for key, value in scoring_service.stage_totals(team).items()}
        rows.append({"team": team.pk, "identifier": team.team_identifier,
                     "before": before, "after": after,
                     "events": [{"id": e.pk, "updated_at": e.updated_at.isoformat(),
                                 "key": e.dedupe_key, "marks": str(e.marks)} for e in events]})
    report = {"competition": competition.pk, "policy_version": POLICY_VERSION,
              "rows": rows, "issues": sorted(set(issues))}
    return report, digest(report)


@transaction.atomic
def enroll_policy(competition, *, window_start, window_end, archive_budget_bytes,
                  recorded_by=None, reviewed_reconciliation=None):
    """Freeze future measurement policy after inspecting existing mark totals."""
    list(Team.objects.select_for_update().filter(competition=competition).order_by("pk"))
    if ScoringPolicyEnrollment.objects.filter(competition=competition).exists():
        raise ValueError("competition is already enrolled")
    now = timezone.now()
    if timezone.is_naive(window_start) or timezone.is_naive(window_end) or not window_start < window_end:
        raise ValueError("an aware positive measurement window is required")
    if window_start < now:
        raise ValueError("enrollment cannot manufacture historical identity; choose a future start")
    if type(archive_budget_bytes) is not int or archive_budget_bytes <= 0:
        raise ValueError("approved evidence capacity must be a positive byte count")
    if grading.check():
        raise ValueError("published rubric disagrees with the shared policy")
    report, report_digest = reconciliation_report(competition)
    if report["issues"]:
        raise ValueError("; ".join(report["issues"]))
    changed = any(
        any(Decimal(row["before"][key]) != Decimal(row["after"][key]) for key in row["before"])
        for row in report["rows"]
    )
    if changed and reviewed_reconciliation != report_digest:
        raise ValueError("review criterion-cap changes with scoring_policy preflight before enrollment")
    from services.scoring_identity import enroll_identity

    enroll_identity(competition, now=now)
    enrollment = ScoringPolicyEnrollment.objects.create(
        competition=competition, policy_version=POLICY_VERSION, policy_sha256=POLICY_SHA256,
        window_start=window_start, window_end=window_end, archive_budget_bytes=archive_budget_bytes,
        enrolled_at=now, reconciled_at=now, recorded_by=recorded_by)
    # New logical keys must not leave old day-specific components additive.
    for team in Team.objects.filter(competition=competition).order_by("pk"):
        for criterion, _, cap, _ in grading.rows_for("main", team):
            events = list(ScoreEvent.objects.filter(team=team, stage="main", criterion=criterion))
            if not events:
                continue
            if len(events) == 1:
                event = events[0]
                event.dedupe_key = f"main:{criterion}"
                event.save(update_fields=["dedupe_key", "updated_at"])
                continue
            override = any(event.override_active for event in events)
            value = (next(event.marks for event in events if event.override_active) if override else
                     min(sum((event.marks for event in events), Decimal(0)), Decimal(cap)))
            for event in events:
                event.marks = Decimal(0)
                event.override_active = False
                if event.dedupe_key == f"main:{criterion}":
                    event.dedupe_key = f"baseline:{event.pk}"
                event.note = "Reconciled into the cumulative criterion component."
                event.save()
                scoring_service._append_revision(event, metadata={"baseline": True})
            scoring_service.award(team, "main", value,
                                  source="judge" if override else events[0].source,
                                  criterion=criterion, dedupe_key=f"main:{criterion}",
                                  note="Reconciled baseline for the cumulative criterion.",
                                  recorded_by=recorded_by, override_active=override,
                                  metadata={"baseline": True})
        scoring_service.recompute(team)
    return enrollment


@transaction.atomic
def finalize_policy(competition):
    enrollment = ScoringPolicyEnrollment.objects.select_for_update().filter(competition=competition).first()
    if enrollment is None:
        raise ValueError("competition is not enrolled in automatic scoring")
    if enrollment.finalized_at:
        return enrollment
    if timezone.now() < enrollment.window_end + timezone.timedelta(seconds=120):
        raise ValueError("the competition window and delivery grace must end first")
    missing = []
    for team in Team.objects.filter(competition=competition, status=Team.Status.APPROVED):
        current = {revision.criterion: revision for revision in scoring_service.visible_events(
            competition, team=team).filter(stage="main") if revision.event.dedupe_key.startswith("main:")}
        for criterion, _, _, _ in grading.rows_for("main", team):
            revision = current.get(criterion)
            if revision is None:
                missing.append(f"{team.team_identifier}:{criterion}")
            elif not revision.override_active and grading.side_key(team) != "application_red" and (
                revision.policy_version != enrollment.policy_version or
                revision.window_end != enrollment.window_end or not revision.evidence
            ):
                missing.append(f"{team.team_identifier}:{criterion}")
    if missing:
        raise ValueError("final measurements or explicit overrides required: " + ", ".join(missing))
    enrollment.finalized_at = timezone.now()
    enrollment.save(update_fields=["finalized_at"])
    return enrollment
