"""Warn teams whose submission deadline is close.

    python manage.py send_deadline_reminders              # 72/48/24/12h notices
    python manage.py send_deadline_reminders --dry-run    # report only

Run hourly from cron:

    0 * * * * cd /srv/jengasec && .venv/bin/python manage.py send_deadline_reminders

Safe to run as often as you like. Every reminder carries a `dedupe_key`
of `deadline:<settings_pk>:<hours>h`, and the database refuses a second
copy -- so a team gets each notice once, however many times this runs.

Only teams that have not yet finalised a submission are warned: telling a
team who submitted three days ago that the deadline is near is noise, and
noise is how notifications get ignored.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team
from competitions.models import CompetitionSettings
from notifications.models import Notification
from services import notification_catalogue as cat
from services import notification_service as ns
from submissions.models import Submission

# Hours before the deadline at which a reminder goes out, most distant
# first. Only the most urgent *due* threshold fires on any given run, so
# starting the cron late sends one warning rather than a backlog of them.
THRESHOLDS_HOURS = (72, 48, 24, 12)  # as for registration

# A team that has reached one of these has submitted something real. A
# proposal returned incomplete has not, and its team needs the reminder.
FINALISED = (
    Submission.Status.SUBMITTED,
    Submission.Status.UNDER_REVIEW,
    Submission.Status.COMPLETED,
)


# The two windows, and the wording each one needs. Proposals close weeks
# before the applications they propose, so they are chased separately.
WINDOWS = (
    ("proposal_deadline", "proposal", "Proposals", True),
    ("submission_deadline", "submission", "Submissions", False),
)


class Command(BaseCommand):
    help = "Notify teams whose proposal or submission deadline is approaching."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report which teams would be warned without notifying them.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        dry_run = options["dry_run"]
        total = 0

        for row in CompetitionSettings.objects.filter(submissions_open=True).select_related("competition"):
            for field, kind, noun, proposals_only in WINDOWS:
                deadline = getattr(row, field)
                # A blank proposal deadline means proposals answer to the
                # submission one, which the second pass already covers.
                if deadline is None or deadline <= now:
                    continue
                remaining = deadline - now
                due = [h for h in THRESHOLDS_HOURS if remaining <= timedelta(hours=h)]
                if not due:
                    continue
                hours = min(due)

                teams = self.teams_without_submission(row.competition, proposals_only=proposals_only)
                if not teams:
                    continue

                deadline_text = timezone.localtime(deadline).strftime("%d %b %Y at %H:%M")
                self.stdout.write(
                    f"{row.competition.name} [{kind}]: {hours}h notice for {len(teams)} team(s) "
                    f"(deadline {deadline_text})"
                )
                if dry_run:
                    total += len(teams)
                    continue

                for team in teams:
                    ns.notify_team(
                        team,
                        subject=(
                            f"{noun.rstrip('s')} deadline {cat.time_left(remaining)}"
                        ),
                        body=(
                            f"{noun} for {row.competition.name} close on "
                            f"{deadline_text} (EAT). Your team has not submitted "
                            f"{'a proposal' if proposals_only else 'its documents'} yet.\n\n"
                            f"The platform clock is what counts, and the close is hard: "
                            f"work uploaded after it is refused."
                        ),
                        link=reverse("submissions:list"),
                        notification_type=Notification.Type.DEADLINE_APPROACHING,
                        dedupe_key=f"deadline:{row.pk}:{kind}:{hours}h",
                    )
                    total += 1

        verb = "would notify" if dry_run else "notified"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} team(s)."))

    @staticmethod
    def teams_without_submission(competition, proposals_only=False):
        """Teams with nothing finalised in the window being chased.

        Scoped by type: a team that filed its proposal has still not filed
        its application, and vice versa.
        """
        from services.submission_service import PROPOSAL_TYPES

        submitted = Submission.objects.filter(
            competition=competition, status__in=FINALISED
        )
        if proposals_only:
            submitted = submitted.filter(submission_type__name__in=PROPOSAL_TYPES)
        else:
            submitted = submitted.exclude(submission_type__name__in=PROPOSAL_TYPES)

        teams = (
            Team.objects.filter(competition=competition)
            .exclude(status=Team.Status.WITHDRAWN)
            .exclude(pk__in=submitted.values("team_id"))
        )
        if proposals_only:
            # Application Red does not submit a proposal at all.
            teams = [t for t in teams if t.submits_proposal]
        return list(teams)
