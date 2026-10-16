"""Putting submissions in front of judges.

An assignment is an `Evaluation` with a judge on it: the judge's review
queue lists exactly those. The model's first pass (`score_proposals`)
lives on a separate, judge-less evaluation; each judge gets their own
copy of its suggestions to accept or override, so two judges never edit
the same scores and the model's original stays untouched.

The model may run before or after a judge is assigned. Assigning copies
whatever suggestions exist; `share_ai_scores` fills in judges assigned
before the model got there.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse

from accounts.roles import JUDGE
from audit.models import AuditLog
from judging.models import CriterionScore, Evaluation
from rubrics.models import Rubric
from services import audit_service
from services import notification_service as ns
from submissions.models import Submission

# Submissions a judge can usefully be given: finalised, not yet decided.
ASSIGNABLE_STATUSES = (Submission.Status.SUBMITTED, Submission.Status.UNDER_REVIEW)


class AssignmentError(Exception):
    """Shown to the organiser as-is."""


def rubric_for(submission):
    return (
        Rubric.objects.filter(
            competition=submission.competition,
            submission_type=submission.submission_type,
            is_active=True,
        )
        .prefetch_related("criteria")
        .first()
    )


def judges():
    """Active judge accounts, with how much open review work each holds."""
    return (
        User.objects.filter(groups__name=JUDGE, is_active=True)
        .annotate(
            open_reviews=Count(
                "evaluations",
                filter=Q(evaluations__status__in=Evaluation.IN_PROGRESS_STATUSES),
            ),
            done_reviews=Count(
                "evaluations",
                filter=Q(evaluations__status__in=Evaluation.DONE_STATUSES),
            ),
        )
        .order_by("first_name", "last_name", "username")
    )


def ai_evaluation(submission, rubric):
    """The model's judge-less first pass, if it has run."""
    return Evaluation.objects.filter(
        submission=submission, rubric=rubric, judge=None,
    ).first()


def _copy_ai(score, source):
    score.ai_score = source.ai_score
    score.confidence = source.confidence
    score.evidence = source.evidence
    score.reasoning = source.reasoning
    if score.judge_score is None:
        score.final_score = source.ai_score


@transaction.atomic
def assign(submission, judge, *, by=None, request=None):
    """Give `submission` to `judge`. Returns (evaluation, created).

    Safe to repeat: assigning someone already assigned changes nothing.
    """
    if not (judge.is_active and judge.groups.filter(name=JUDGE).exists()):
        raise AssignmentError(f"{judge.get_full_name() or judge.username} is not an active judge.")
    if submission.status not in ASSIGNABLE_STATUSES:
        raise AssignmentError(
            f"{submission.team.team_name}'s {submission.submission_type.name} is "
            f"{submission.get_status_display().lower()}, not awaiting review."
        )
    rubric = rubric_for(submission)
    if rubric is None or not rubric.criteria.exists():
        raise AssignmentError(
            f"No active rubric for {submission.submission_type.name} -- publish one "
            f"in Rubrics before assigning judges."
        )

    evaluation, created = Evaluation.objects.get_or_create(
        submission=submission, judge=judge, rubric=rubric,
    )
    if not created:
        return evaluation, False

    source = ai_evaluation(submission, rubric)
    suggestions = (
        {s.criterion_id: s for s in source.criterion_scores.all()} if source else {}
    )
    for criterion in rubric.criteria.all():
        score = CriterionScore(evaluation=evaluation, criterion=criterion)
        if criterion.pk in suggestions:
            _copy_ai(score, suggestions[criterion.pk])
        score.save()
    if source and source.status in (Evaluation.Status.AI_COMPLETE, *Evaluation.DONE_STATUSES):
        evaluation.status = Evaluation.Status.AI_COMPLETE
        evaluation.save(update_fields=["status", "updated_at"])
    evaluation.recalculate_total()

    audit_service.record(
        AuditLog.Action.JUDGE_ASSIGNED, request=request, actor=by, target=evaluation,
        description=f"{judge.username} assigned to {submission}",
    )
    ns.notify_user(
        judge,
        subject=f"New review: {submission.team.team_name} -- {submission.submission_type.name}",
        body=(
            "A submission has been added to your review queue. "
            + ("The model's suggested scores are ready for you to check."
               if suggestions else
               "The model has not scored it yet; you can score it yourself or wait.")
        ),
        link=reverse("judging:ai_result_detail", args=[evaluation.pk]),
        notification_type=ns.Notification.Type.GENERAL,
        send_email=True,
        dedupe_key=f"judge-assigned:{evaluation.pk}",
    )
    return evaluation, True


def can_unassign(evaluation):
    """Only before the judge has touched it: their scores are a record."""
    if evaluation.status in Evaluation.DONE_STATUSES:
        return False
    if evaluation.status == Evaluation.Status.JUDGE_REVIEWING:
        return False
    return not evaluation.criterion_scores.filter(judge_score__isnull=False).exists()


@transaction.atomic
def unassign(evaluation, *, by=None, request=None):
    if evaluation.judge_id is None:
        raise AssignmentError("That is the model's own evaluation, not an assignment.")
    if not can_unassign(evaluation):
        raise AssignmentError(
            f"{evaluation.judge.get_full_name() or evaluation.judge.username} has already "
            f"started this review, so it stays with them."
        )
    audit_service.record(
        AuditLog.Action.JUDGE_UNASSIGNED, request=request, actor=by, target=evaluation,
        description=f"{evaluation.judge.username} removed from {evaluation.submission}",
    )
    evaluation.delete()


def distribute(submissions, *, per_submission=1, by=None, request=None):
    """Top each submission up to `per_submission` judges, least-loaded
    judge first. Returns (assigned, skipped_messages)."""
    pool = list(judges())
    if not pool:
        raise AssignmentError("There are no active judge accounts to assign.")
    load = {j.pk: j.open_reviews for j in pool}
    assigned, skipped = 0, []
    for submission in submissions:
        have = set(
            submission.evaluations.exclude(judge=None).values_list("judge_id", flat=True)
        )
        needed = per_submission - len(have)
        candidates = sorted(
            (j for j in pool if j.pk not in have), key=lambda j: (load[j.pk], j.pk)
        )
        for judge in candidates[:max(needed, 0)]:
            try:
                _, created = assign(submission, judge, by=by, request=request)
            except AssignmentError as exc:
                skipped.append(str(exc))
                break
            if created:
                assigned += 1
                load[judge.pk] += 1
    return assigned, skipped


def share_ai_scores(submission, rubric):
    """After the model runs, pass its suggestions to judges already assigned.

    Never touches a score a judge has set, nor a review already decided.
    """
    source = ai_evaluation(submission, rubric)
    if source is None:
        return 0
    suggestions = {s.criterion_id: s for s in source.criterion_scores.all()}
    updated = 0
    for evaluation in Evaluation.objects.filter(
        submission=submission, rubric=rubric, judge__isnull=False,
    ).exclude(status__in=Evaluation.DONE_STATUSES):
        for criterion_id, suggestion in suggestions.items():
            score, _ = CriterionScore.objects.get_or_create(
                evaluation=evaluation, criterion_id=criterion_id,
            )
            _copy_ai(score, suggestion)
            score.save()
        if evaluation.status == Evaluation.Status.PENDING:
            evaluation.status = Evaluation.Status.AI_COMPLETE
            evaluation.save(update_fields=["status", "updated_at"])
        evaluation.recalculate_total()
        updated += 1
    return updated
