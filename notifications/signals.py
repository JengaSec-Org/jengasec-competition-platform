"""Event wiring for Module 12.

Every handler lives here rather than in the module that owns the event:
this app listens, it does not ask to be called. The one edit made
elsewhere was deleting the hand-rolled `Notification.bulk_create` inside
`submission_service.finalize()` -- it notified only the captain and the
uploader, marked itself as emailed without sending anything, and would
now fire alongside `on_submission_finalized` below.

Two rules hold throughout:

* **Idempotency is structural.** Each handler supplies a `dedupe_key` and
  the database refuses the second copy. Handlers are therefore safe to fire
  more than once -- a re-saved model, a retried request, a signal wired up
  twice.
* **Nothing is created until the transaction commits.** `finalize()` and
  friends run inside `transaction.atomic`; a notification sent from inside
  that block would survive a rollback and tell a team their submission
  landed when it did not.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.urls import reverse

from accounts.models import Team, TeamMember
from competitions.models import CompetitionSettings
from judging.models import Appeal, Evaluation
from notifications.models import Notification
from services import notification_catalogue as cat
from services import notification_service as ns
from submissions.signals import submission_finalized

logger = logging.getLogger(__name__)


def _on_commit(func):
    """Run `func` once the surrounding transaction commits (or now, if none)."""
    transaction.on_commit(func)


# ---------------------------------------------------------------------------
# Submission Uploaded
# ---------------------------------------------------------------------------

@receiver(submission_finalized, dispatch_uid="notifications.submission_finalized")
def on_submission_finalized(sender, submission, actor=None, **kwargs):
    """A team finalised a submission: confirm to them, alert the reviewers.

    Keyed on the version, so finalising v1 and later v2 both notify -- they
    are genuinely different events -- while re-finalising v1 does not.
    """
    team = submission.team
    version = submission.current_version
    detail = reverse("submissions:detail", args=[submission.pk])
    label = f"{team.team_name} :: {submission.submission_type.name}"

    def send():
        ns.notify_team(
            team,
            subject=f"Submission received: {submission.submission_type.name}",
            body=(
                f"Version {version} of your {submission.submission_type.name} for "
                f"{submission.competition.name} has been received and is queued "
                f"for review."
            ),
            link=detail,
            notification_type=Notification.Type.SUBMISSION_CONFIRMED,
            dedupe_key=f"submission:{submission.pk}:v{version}:confirmed",
        )
        ns.notify_admins(
            subject="New submission received",
            body=f"{label} (v{version}) was submitted for {submission.competition.name}.",
            link=detail,
            notification_type=Notification.Type.SUBMISSION_CONFIRMED,
            dedupe_key=f"submission:{submission.pk}:v{version}:admin",
        )
        ns.notify_judges(
            subject="New submission awaiting review",
            body=f"{label} (v{version}) is ready to score.",
            link=reverse("dashboard:insights_dashboard"),
            notification_type=Notification.Type.SUBMISSION_CONFIRMED,
            dedupe_key=f"submission:{submission.pk}:v{version}:judge",
        )

    _on_commit(send)


# ---------------------------------------------------------------------------
# Evaluation Complete
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Evaluation, dispatch_uid="notifications.evaluation_saved")
def on_evaluation_saved(sender, instance, **kwargs):
    """An evaluation reached a finished state.

    No transition tracking: the handler fires on every save of a finished
    evaluation and the `dedupe_key` guarantees exactly one notification per
    evaluation. That keeps the logic out of `judging.models`, which belongs
    to another module.

    Deliberately silent while `results_published` is off -- a team learning
    their score early, from a notification, would undercut the organisers'
    control of the release.
    """
    if instance.status not in Evaluation.DONE_STATUSES:
        return

    submission = instance.submission
    # `Competition.settings` is a property that creates the row on first
    # access, so this is always safe to read.
    results_published = submission.competition.settings.results_published
    detail = reverse("submissions:detail", args=[submission.pk])

    def send():
        ns.notify_admins(
            subject="Evaluation complete",
            body=(
                f"{submission.team.team_name} :: {submission.submission_type.name} "
                f"has been scored."
            ),
            link=detail,
            notification_type=Notification.Type.EVAL_COMPLETE,
            dedupe_key=f"evaluation:{instance.pk}:admin",
        )
        if results_published:
            ns.notify_team(
                submission.team,
                subject="Your submission has been evaluated",
                body=(
                    f"Scoring is complete for your {submission.submission_type.name} "
                    f"in {submission.competition.name}. Results are now available."
                ),
                link=detail,
                notification_type=Notification.Type.EVAL_COMPLETE,
                dedupe_key=f"evaluation:{instance.pk}:team",
            )

    _on_commit(send)


# ---------------------------------------------------------------------------
# Appeal Submitted / Resolved
# ---------------------------------------------------------------------------

# An organiser's decision. WITHDRAWN is excluded on purpose: the team did
# that themselves and does not need telling.
APPEAL_DECIDED = (Appeal.Status.UPHELD, Appeal.Status.REJECTED)


@receiver(post_save, sender=Appeal, dispatch_uid="notifications.appeal_saved")
def on_appeal_saved(sender, instance, created, **kwargs):
    """A team filed an appeal, or organisers decided one."""
    if not created:
        if instance.status in APPEAL_DECIDED:
            _appeal_resolved(instance)
        return

    submission = instance.submission
    detail = reverse("submissions:detail", args=[submission.pk])

    def send():
        ns.notify_admins(
            subject="Appeal submitted",
            body=(
                f"{instance.team.team_name} has appealed the result for "
                f"{submission.submission_type.name}. Grounds: {instance.reason[:200]}"
            ),
            link=detail,
            notification_type=Notification.Type.APPEAL_SUBMITTED,
            dedupe_key=f"appeal:{instance.pk}:admin",
        )
        ns.notify_team(
            instance.team,
            subject="Appeal received",
            body=(
                "Your appeal has been logged and will be reviewed by the "
                "organisers. You will be notified once it is resolved."
            ),
            link=detail,
            notification_type=Notification.Type.APPEAL_SUBMITTED,
            dedupe_key=f"appeal:{instance.pk}:team",
        )

    _on_commit(send)


def _appeal_resolved(appeal):
    """Organisers decided an appeal: tell the team the outcome and the why.

    The resolution text travels with the notification rather than sending
    people back to the site to find out what was decided -- an appeal is
    exactly the moment somebody deserves a straight answer.

    Keyed once per appeal: an appeal decided, reopened and decided
    differently notifies only the first time. Rare enough to accept, and
    the alternative is a key that lets a status flip-flop spam the team.
    """
    detail = reverse("submissions:detail", args=[appeal.submission_id])
    outcome = appeal.get_status_display()
    resolution = appeal.resolution or "No further detail was recorded."

    def send():
        ns.notify_team(
            appeal.team,
            subject=f"Appeal {outcome.lower()}",
            body=(
                f"Your appeal regarding "
                f"{appeal.submission.submission_type.name} has been "
                f"{outcome.lower()}.\n\n{resolution}"
            ),
            link=detail,
            notification_type=Notification.Type.APPEAL_RESOLVED,
            dedupe_key=f"appeal:{appeal.pk}:resolved",
        )

    _on_commit(send)


# ---------------------------------------------------------------------------
# Results Published
# ---------------------------------------------------------------------------

@receiver(post_save, sender=CompetitionSettings, dispatch_uid="notifications.settings_saved")
def on_competition_settings_saved(sender, instance, **kwargs):
    """Organisers released results.

    Without this the platform goes silent at exactly the wrong moment:
    `on_evaluation_saved` withholds the team notification while results are
    unpublished, and publishing saves CompetitionSettings rather than any
    Evaluation -- so nothing picked the deferred announcement back up.

    Only teams with a finished evaluation hear. "Results are out" means
    nothing to a team that has no result.
    """
    if not instance.results_published:
        return

    competition = instance.competition
    teams = list(
        Team.objects.filter(
            competition=competition,
            submissions__evaluations__status__in=Evaluation.DONE_STATUSES,
        ).distinct()
    )
    if not teams:
        return

    def send():
        for team in teams:
            ns.notify_team(
                team,
                subject=f"Results published: {competition.name}",
                body=(
                    f"Scores and feedback for {competition.name} are now "
                    f"available to your team."
                ),
                link=reverse("submissions:list"),
                notification_type=Notification.Type.RESULTS_PUBLISHED,
                # Once per competition: an accidental unpublish/publish
                # cannot re-email everyone. A genuine re-publication after a
                # correction therefore has to be announced another way.
                dedupe_key=f"results:{competition.pk}:published",
            )

    _on_commit(send)


# ---------------------------------------------------------------------------
# Registration submitted / approved / rejected
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Team, dispatch_uid="notifications.team_saved")
def on_team_saved(sender, instance, created, **kwargs):
    """Registration outcomes.

    The receipt is no longer sent on creation: creating a team is the
    start of assembling one, and the receipt goes out when the captain
    submits it (`team_service.submit_registration`). Status handlers fire
    on every save and rely on `dedupe_key` for once-only delivery, the
    same pattern as evaluations.
    """
    def send():
        if created:
            return
        if instance.status == Team.Status.APPROVED:
            assignment = instance.target_assignments.first()
            spec = competition_spec(instance)
            cat.registration_approved(
                instance,
                assignment=assignment,
                resources=spec.get("provided", ()) if spec else (),
            )
        elif instance.status == Team.Status.REJECTED:
            cat.registration_rejected(instance)

    _on_commit(send)


def competition_spec(team):
    """Track specification for a team, imported lazily to avoid a cycle."""
    from services import competition_service

    return competition_service.spec_for(team)


# ---------------------------------------------------------------------------
# Member removed or withdrew
# ---------------------------------------------------------------------------

@receiver(post_delete, sender=TeamMember, dispatch_uid="notifications.member_deleted")
def on_member_deleted(sender, instance, **kwargs):
    """A member left. Captain and member both hear, with the consequence.

    Read off the instance before the transaction closes -- the row is gone
    by the time the callback runs, so nothing may be lazily fetched later.
    """
    team = instance.team
    name = instance.student_name
    member_user = instance.user
    withdrew = team.status == Team.Status.WITHDRAWN

    def send():
        # The team itself may have been deleted along with its members; a
        # cascade is not a departure and there is nobody left to tell.
        if not Team.objects.filter(pk=team.pk).exists():
            return
        cat.membership_changed(team, name, member_user=member_user, withdrew=withdrew)

    _on_commit(send)
