"""Feedback reports and appeals (Entry Guide section 10).

Every scored proposal produces a feedback report -- criterion scores,
comments, evidence, penalties -- visible to the team once results are
published. Appeals are procedural only, open for 72 hours from the
publication stamp, and are reviewed by a judge who did not score the work.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import UserProfile
from judging.models import Appeal, Evaluation

User = get_user_model()


class AppealError(ValueError):
    """Message is written for the team and safe to show verbatim."""


def feedback_report(submission):
    """The finished evaluations for a submission, with their criterion rows.

    Empty until results are published for the competition -- the report is
    the team's view, and the organisers control the release.
    """
    settings_row = submission.competition.settings
    if not settings_row.results_published:
        return []
    evaluations = (
        submission.evaluations.filter(status__in=Evaluation.DONE_STATUSES)
        .select_related("judge", "rubric")
        .prefetch_related("criterion_scores__criterion")
    )
    report = []
    for evaluation in evaluations:
        zeroed = evaluation.zeroed_criterion_keywords()
        rows = []
        for score in evaluation.criterion_scores.all().order_by("criterion__display_order"):
            name = score.criterion.criterion
            rows.append({
                "criterion": name,
                "max_score": score.criterion.max_score,
                "weight": score.criterion.weight,
                "score": score.final_score,
                "zeroed": any(k in name.lower() for k in zeroed),
                "comments": score.comments,
                "evidence": score.evidence,
                "reasoning": score.reasoning,
            })
        report.append({"evaluation": evaluation, "rows": rows})
    return report


def appeal_window(submission):
    """(open, closes_at). Open only inside the 72 hours after publication."""
    settings_row = submission.competition.settings
    closes_at = settings_row.appeal_window_closes_at
    if closes_at is None:
        return False, None
    return timezone.now() <= closes_at, closes_at


@transaction.atomic
def file_appeal(submission, user, grounds, reason):
    """A captain disputes a published result on procedural grounds."""
    team = submission.team
    if team.captain_id != user.pk:
        raise AppealError("Only the team captain can file an appeal.")
    if not submission.evaluations.filter(status__in=Evaluation.DONE_STATUSES).exists():
        raise AppealError("There is no published result to appeal yet.")
    is_open, closes_at = appeal_window(submission)
    if not is_open:
        if closes_at is None:
            raise AppealError("Results have not been published; nothing to appeal yet.")
        raise AppealError(
            f"Entry Guide section 10: appeals close 72 hours after results are "
            f"published -- that was {closes_at:%d %b %Y at %H:%M}."
        )
    if grounds not in Appeal.Grounds.values:
        raise AppealError("Choose one of the procedural grounds listed.")
    if submission.appeals.filter(status__in=(Appeal.Status.OPEN, Appeal.Status.UNDER_REVIEW)).exists():
        raise AppealError("An appeal for this submission is already open.")
    reason = (reason or "").strip()
    if len(reason) < 50:
        raise AppealError("Set out the procedural failure in at least 50 characters.")
    return Appeal.objects.create(
        submission=submission, team=team, grounds=grounds, reason=reason, created_by=user
    )


def first_judges(submission):
    return set(
        submission.evaluations.exclude(judge__isnull=True).values_list("judge_id", flat=True)
    )


def eligible_second_judges(submission):
    """Judges who did not score this submission."""
    return (
        User.objects.filter(profile__role=UserProfile.Role.JUDGE, is_active=True)
        .exclude(pk__in=first_judges(submission))
        .order_by("username")
    )


def assign_second_judge(appeal, judge, by=None):
    if judge.pk in first_judges(appeal.submission):
        raise AppealError(
            f"{judge.get_full_name() or judge.username} scored this submission and "
            f"cannot review its appeal."
        )
    appeal.second_judge = judge
    if appeal.status == Appeal.Status.OPEN:
        appeal.status = Appeal.Status.UNDER_REVIEW
    appeal.save(update_fields=["second_judge", "status"])
    return appeal
