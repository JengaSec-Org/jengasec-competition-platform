"""Feedback reports and procedural appeals for submissions and score revisions.

Every scored proposal produces a feedback report -- criterion scores,
comments, evidence, penalties -- visible to the team once results are
published. Appeals use the configured period after publication and require
an independent judge. A decision does not itself change any marks.
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


ACTIVE_STATUSES = (Appeal.Status.OPEN, Appeal.Status.UNDER_REVIEW)
MAX_EXPLANATION_LENGTH = 5000


def _competition(subject):
    if isinstance(subject, Appeal):
        return subject.competition
    if hasattr(subject, "event_id"):
        return subject.event.competition
    return subject.competition


def appeal_window(subject):
    """Use the competition's configured publication stamp and appeal period."""
    settings_row = _competition(subject).settings
    closes_at = settings_row.appeal_window_closes_at
    published_at = settings_row.results_published_at
    if not settings_row.results_published or published_at is None or closes_at is None:
        return False, closes_at
    return published_at <= timezone.now() <= closes_at, closes_at


def _captain(team, user):
    if not user.is_authenticated or not user.is_active or team.captain_id != user.pk:
        raise AppealError("Only the team captain can file or withdraw an appeal.")


def _explanation(value, *, minimum=50):
    if not isinstance(value, str):
        raise AppealError("Give a written explanation.")
    value = value.strip()
    if not minimum <= len(value) <= MAX_EXPLANATION_LENGTH:
        raise AppealError(f"Give an explanation of {minimum} to {MAX_EXPLANATION_LENGTH} characters.")
    return value


def _filing_checks(subject, user, grounds, reason):
    _captain(subject.team, user)
    is_open, closes_at = appeal_window(subject)
    settings_row = _competition(subject).settings
    if not is_open:
        if closes_at is None or not settings_row.results_published:
            raise AppealError("Results have not been published; nothing to appeal yet.")
        hours = settings_row.appeal_window_days * 24
        raise AppealError(
            f"Appeals close {hours} hours after results are published; "
            f"the configured window closes {closes_at:%d %b %Y at %H:%M}."
        )
    if grounds not in Appeal.Grounds.values:
        raise AppealError("Choose one of the procedural grounds listed.")
    return _explanation(reason)


@transaction.atomic
def file_appeal(submission, user, grounds, reason):
    """Keep the submission appeal entry point and its original result link."""
    from submissions.models import Submission

    submission = Submission.objects.select_for_update().select_related("team", "competition").get(pk=submission.pk)
    reason = _filing_checks(submission, user, grounds, reason)
    if not submission.evaluations.filter(status__in=Evaluation.DONE_STATUSES).exists():
        raise AppealError("There is no published result to appeal yet.")
    if submission.appeals.filter(status__in=ACTIVE_STATUSES).exists():
        raise AppealError("An appeal for this submission is already open.")
    return Appeal.objects.create(
        submission=submission, team=submission.team, grounds=grounds, reason=reason, created_by=user)


def _evidence_ids(revision):
    return sorted({str(reference["id"]) for reference in revision.evidence})


def _hold_evidence(appeal, revision):
    from scoring.models import ScoringEvidence

    ids = _evidence_ids(revision)
    archives = list(ScoringEvidence.objects.select_for_update().filter(
        pk__in=ids, competition=appeal.competition, team=appeal.team).order_by("pk"))
    available = {str(archive.pk) for archive in archives}
    appeal.evidence_gaps = [identifier for identifier in ids if identifier not in available]
    if appeal.evidence_gaps:
        appeal.save(update_fields=["evidence_gaps"])
    deadline = appeal.competition.settings.appeal_window_closes_at
    for archive in archives:
        archive.appeal_hold = True
        if deadline is not None and (archive.retain_until is None or archive.retain_until < deadline):
            archive.retain_until = deadline
        archive.save(update_fields=["appeal_hold", "retain_until"])
    appeal.retained_evidence.add(*archives)


@transaction.atomic
def file_score_appeal(revision, user, grounds, reason):
    """Bind the appeal to accepted bytes and one logical score component."""
    from scoring.models import ScoreEvent, ScoreRevision

    ScoreEvent.objects.select_for_update().get(pk=revision.event_id)
    revision = ScoreRevision.objects.select_related("event__team", "event__competition").get(pk=revision.pk)
    reason = _filing_checks(revision, user, grounds, reason)
    if Appeal.objects.filter(score_event_id=revision.event_id, status__in=ACTIVE_STATUSES).exists():
        raise AppealError("An appeal for this score component is already open.")
    appeal = Appeal.objects.create(score_revision=revision, score_event=revision.event,
                                   team=revision.team, grounds=grounds, reason=reason, created_by=user)
    _hold_evidence(appeal, revision)
    return appeal


def _subject(appeal):
    return appeal.score_revision if appeal.score_revision_id else appeal.submission


def first_judges(subject):
    if isinstance(subject, Appeal):
        subject = _subject(subject)
    if hasattr(subject, "event_id"):
        return set(subject.event.revisions.exclude(recorded_by__isnull=True).values_list("recorded_by_id", flat=True))
    return set(subject.evaluations.exclude(judge__isnull=True).values_list("judge_id", flat=True))


def eligible_second_judges(subject):
    """An active judge who neither made the decision nor belongs to the team."""
    if isinstance(subject, Appeal):
        subject = _subject(subject)
    return (User.objects.filter(profile__role=UserProfile.Role.JUDGE, is_active=True)
            .exclude(pk__in=first_judges(subject)).exclude(pk=subject.team.captain_id)
            .exclude(team_memberships__team=subject.team).order_by("username").distinct())


def _locked_appeal(appeal):
    if appeal.score_event_id:
        from scoring.models import ScoreEvent
        ScoreEvent.objects.select_for_update().get(pk=appeal.score_event_id)
    else:
        from submissions.models import Submission
        Submission.objects.select_for_update().get(pk=appeal.submission_id)
    return Appeal.objects.select_for_update(of=("self",)).select_related(
        "score_revision__event__competition", "score_revision__event__team", "submission__competition", "team"
    ).get(pk=appeal.pk)


@transaction.atomic
def assign_second_judge(appeal, judge, by=None):
    appeal = _locked_appeal(appeal)
    if by is not None and not (by.is_active and (by.is_staff or by.is_superuser)):
        raise AppealError("Only an organiser can assign an appeal reviewer.")
    if appeal.status not in ACTIVE_STATUSES:
        raise AppealError("A closed appeal cannot be reassigned.")
    if judge.pk in first_judges(appeal):
        label = "score component" if appeal.score_revision_id else "submission"
        raise AppealError(f"{judge.get_full_name() or judge.username} scored this {label} and cannot review its appeal.")
    if not eligible_second_judges(appeal).filter(pk=judge.pk).exists():
        raise AppealError("Choose an independent active judge outside the appealing team.")
    appeal.second_judge = judge
    appeal.status = Appeal.Status.UNDER_REVIEW
    appeal.save(update_fields=["second_judge", "status"])
    return appeal


def _release_evidence(appeal):
    archives = list(appeal.retained_evidence.select_for_update().order_by("pk"))
    deadline = appeal.competition.settings.appeal_window_closes_at
    for archive in archives:
        archive.appeal_hold = archive.score_appeals.filter(status__in=ACTIVE_STATUSES).exists()
        if deadline is not None and (archive.retain_until is None or archive.retain_until < deadline):
            archive.retain_until = deadline
        archive.save(update_fields=["appeal_hold", "retain_until"])


@transaction.atomic
def resolve_appeal(appeal, user, status, resolution):
    """Record an independent decision. Any score correction is a separate act."""
    appeal = _locked_appeal(appeal)
    if appeal.status not in ACTIVE_STATUSES:
        raise AppealError("This appeal is already closed.")
    if appeal.second_judge_id != user.pk or not eligible_second_judges(appeal).filter(pk=user.pk).exists():
        raise AppealError("Only the assigned independent judge can resolve this appeal.")
    if status not in (Appeal.Status.UPHELD, Appeal.Status.REJECTED):
        raise AppealError("Choose upheld or rejected.")
    appeal.resolution = _explanation(resolution, minimum=20)
    appeal.status = status
    appeal.resolved_by = user
    appeal.resolved_at = timezone.now()
    appeal.save(update_fields=["resolution", "status", "resolved_by", "resolved_at"])
    _release_evidence(appeal)
    return appeal


@transaction.atomic
def withdraw_appeal(appeal, user, reason):
    appeal = _locked_appeal(appeal)
    _captain(appeal.team, user)
    if appeal.status not in ACTIVE_STATUSES:
        raise AppealError("This appeal is already closed.")
    appeal.status = Appeal.Status.WITHDRAWN
    appeal.resolution = "Withdrawn by the captain: " + _explanation(reason, minimum=20)
    appeal.resolved_by = user
    appeal.resolved_at = timezone.now()
    appeal.save(update_fields=["resolution", "status", "resolved_by", "resolved_at"])
    _release_evidence(appeal)
    return appeal
