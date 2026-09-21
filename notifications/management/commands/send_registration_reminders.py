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

def outstanding_for(team):
    """What still stands between this team and a complete registration.

    Entry Guide section 2 and the rejection reasons in section 6. Literal
    on purpose: the captain gets this list verbatim, so each entry has to
    name something they can act on.
    """
    from services.team_service import MAX_MEMBERS, MIN_MEMBERS, playing_count

    missing = []
    if not team.captain:
        missing.append("No captain is linked to a platform account.")
    count = playing_count(team)
    if count < MIN_MEMBERS:
        missing.append(
            f"Only {count} member{'s' if count != 1 else ''} on the roster; "
            f"teams are {MIN_MEMBERS} to {MAX_MEMBERS} people."
        )
    unlinked = team.members.filter(user__isnull=True).count()
    if unlinked:
        missing.append(
            f"{unlinked} member{'s have' if unlinked != 1 else ' has'} not accepted "
            f"their invitation yet (invitations expire after seven days)."
        )
    pending = team.invitations.filter(status="pending", expires_at__gt=timezone.now()).count()
    if pending:
        missing.append(
            f"{pending} invitation{'s are' if pending != 1 else ' is'} still waiting to be "
            f"accepted; a registration cannot be submitted with invitations open."
        )
    if not team.members.filter(is_security_specialist=True).exists():
        missing.append("No member is marked as the team's security specialist.")
    if not team.track:
        missing.append("No track chosen (Application or AI).")
    if team.track == team.Track.APPLICATION and team.team_type == team.TeamType.BLUE and not team.application_brief_id:
        missing.append("No JengaBank brief chosen.")
    if not team.institution:
        missing.append("Lead institution not set.")
    if team.mentor_email and not team.mentor_name:
        missing.append("Mentor named by email but without a name.")
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
