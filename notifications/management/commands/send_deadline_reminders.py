"""Warn teams whose submission deadline is close.

    python manage.py send_deadline_reminders              # 72h and 24h notices
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
from services import notification_service as ns
from submissions.models import Submission

# Hours before the deadline at which a reminder goes out, most distant
# first. Only the most urgent *due* threshold fires on any given run, so
# starting the cron late sends one warning rather than a backlog of them.
THRESHOLDS_HOURS = (72, 24)

# A team that has reached one of these has submitted something real.
FINALISED = (
    Submission.Status.SUBMITTED,
    Submission.Status.UNDER_REVIEW,
    Submission.Status.COMPLETED,
)


class Command(BaseCommand):
    help = "Notify teams whose submission deadline is approaching."

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

        rows = CompetitionSettings.objects.filter(
            submissions_open=True,
            submission_deadline__isnull=False,
            submission_deadline__gt=now,
        ).select_related("competition")

        for row in rows:
            remaining = row.submission_deadline - now
            due = [h for h in THRESHOLDS_HOURS if remaining <= timedelta(hours=h)]
            if not due:
                continue
            hours = min(due)

            teams = self.teams_without_submission(row.competition)
            if not teams:
                continue

            deadline_text = row.submission_deadline.strftime("%d %b %Y at %H:%M")
            self.stdout.write(
                f"{row.competition.name}: {hours}h notice for {len(teams)} team(s) "
                f"(deadline {deadline_text})"
            )
            if dry_run:
                total += len(teams)
                continue

            for team in teams:
                ns.notify_team(
                    team,
                    subject=f"Submission deadline in {hours} hours",
                    body=(
                        f"Submissions for {row.competition.name} close on "
                        f"{deadline_text}. Your team has not submitted yet."
                    ),
                    link=reverse("submissions:list"),
                    notification_type=Notification.Type.DEADLINE_APPROACHING,
                    dedupe_key=f"deadline:{row.pk}:{hours}h",
                )
                total += 1

        verb = "would notify" if dry_run else "notified"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} team(s)."))

    @staticmethod
    def teams_without_submission(competition):
        submitted = Submission.objects.filter(
            competition=competition, status__in=FINALISED
        ).values("team_id")
        return list(
            Team.objects.filter(competition=competition)
            .exclude(status=Team.Status.WITHDRAWN)
            .exclude(pk__in=submitted)
        )
