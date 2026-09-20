"""Warn captains of incomplete teams that registration is closing.

    python manage.py send_registration_reminders
    python manage.py send_registration_reminders --dry-run

Run hourly from cron:

    30 * * * * cd /srv/jengasec && .venv/bin/python manage.py send_registration_reminders

Notices go out at 7 days, 2 days and 12 hours before
`CompetitionSettings.registration_deadline`. Only the most urgent *due*
threshold fires on a run, so starting the cron late sends one warning
rather than a backlog of three. Each notice is deduplicated per team and
threshold, so running this more often changes nothing.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Team
from competitions.models import CompetitionSettings
from services import notification_catalogue as cat

# Hours before the registration deadline at which a captain is warned.
THRESHOLDS_HOURS = (168, 48, 12)  # 7 days, 2 days, 12 hours

# Smallest team the rules accept. Kept here rather than in the model so
# organisers can change it without a migration.
MIN_MEMBERS = 4


def outstanding_for(team):
    """What still stands between this team and a complete registration.

    Deliberately literal: the captain gets this list verbatim, so each
    entry has to name something they can act on.
    """
    missing = []
    if not team.captain:
        missing.append("No captain is linked to a platform account.")
    member_count = team.members.count()
    if member_count < MIN_MEMBERS:
        missing.append(
            f"Only {member_count} member{'s' if member_count != 1 else ''} listed; "
            f"at least {MIN_MEMBERS} are required."
        )
    unlinked = team.members.filter(user__isnull=True).count()
    if unlinked:
        missing.append(
            f"{unlinked} member{'s have' if unlinked != 1 else ' has'} not yet "
            f"created a platform account."
        )
    if not team.track:
        missing.append("No track chosen (Cloud, Application or AI).")
    if not team.institution:
        missing.append("Institution not set.")
    return missing


class Command(BaseCommand):
    help = "Remind captains of incomplete teams before registration closes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report which captains would be warned without notifying them.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        dry_run = options["dry_run"]
        total = 0

        rows = CompetitionSettings.objects.filter(
            registration_deadline__isnull=False,
            registration_deadline__gt=now,
        ).select_related("competition")

        for row in rows:
            remaining = row.registration_deadline - now
            due = [h for h in THRESHOLDS_HOURS if remaining <= timedelta(hours=h)]
            if not due:
                continue
            hours = min(due)

            # Rejected teams are included on purpose: the rejection notice
            # tells them to correct and resubmit before the window shuts, so
            # they are exactly the teams that most need the countdown.
            # Approved and withdrawn teams have nothing left to do.
            teams = Team.objects.filter(
                competition=row.competition,
                status__in=(Team.Status.REGISTERED, Team.Status.REJECTED),
            ).exclude(captain__isnull=True)

            for team in teams:
                missing = outstanding_for(team)
                if not missing:
                    continue
                self.stdout.write(
                    f"{row.competition.name} / {team.team_name}: {hours}h notice, "
                    f"{len(missing)} item(s) outstanding"
                )
                if dry_run:
                    total += 1
                    continue
                cat.registration_closing(team, hours, missing)
                total += 1

        verb = "would warn" if dry_run else "warned"
        self.stdout.write(self.style.SUCCESS(f"{verb} {total} captain(s)."))
