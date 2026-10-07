"""Chase the two things that stop a team existing: empty seats, and no team.

    python manage.py send_roster_call                 # preview both
    python manage.py send_roster_call --confirm       # send both
    python manage.py send_roster_call --only short    # teams under four
    python manage.py send_roster_call --only teamless # people with no team

Two audiences, two different asks, one command because they are the same
problem seen from each end: a team a seat short and a person without a
team are each other's answer.

Separate from `send_briefing`, which carries everything at once. This is
for sending the roster call on its own, from the notification console,
without re-sending the rest.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Team, TeamMember
from competitions.models import Competition
from services import matching_service
from services import notification_catalogue as cat


class Command(BaseCommand):
    help = "Email teams that are short of four, and competitors with no team."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm", action="store_true",
            help="Send it. Without this, report who would get what.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Explicit no-op form.")
        parser.add_argument(
            "--only", choices=("short", "teamless"), default="",
            help="Limit to one of the two audiences.",
        )

    def handle(self, *args, **options):
        send = options["confirm"] and not options["dry_run"]
        only = options["only"]
        site = (getattr(settings, "SITE_URL", "") or "").rstrip("/")

        competition = Competition.objects.order_by("-start_date").first()
        if competition is None:
            self.stdout.write(self.style.WARNING("No competition; nothing to do."))
            return
        settings_row = competition.settings

        short_rows, teamless_rows = [], []

        if only != "teamless":
            for team in matching_service.teams_with_seats(competition, open_only=False):
                seats = matching_service.seats_free(team)
                if not seats:
                    continue
                # Their own deadline when they have one -- Application Red
                # register after qualifying, so the general date is not theirs.
                deadline = (
                    settings_row.red_roster_deadline
                    if not team.submits_proposal and settings_row.red_roster_deadline
                    else settings_row.roster_deadline
                )
                for member in team.members.exclude(user__isnull=True).select_related("user"):
                    if not member.user.email:
                        continue
                    short_rows.append({
                        "user": member.user,
                        "team": team,
                        "seats": seats,
                        "deadline": deadline,
                        "is_captain": team.captain_id == member.user_id,
                    })

        if only != "short":
            seated = TeamMember.objects.exclude(user__isnull=True).values("user_id")
            from services.account_service import competitor_accounts

            for user in (
                competitor_accounts()
                .exclude(pk__in=seated)
                .select_related("profile")
                .order_by("username")
                .distinct()
            ):
                if not user.email:
                    continue
                profile = getattr(user, "profile", None)
                teamless_rows.append({
                    "user": user,
                    "deadline": settings_row.roster_deadline,
                    "side_declared": bool(profile and profile.role),
                })

        self._report("Teams short of four", short_rows, lambda r: (
            f"{r['user'].username} <{r['user'].email}> - {r['team'].team_name}: "
            f"{r['seats']} seat(s) free{' [captain]' if r['is_captain'] else ''}"
        ))
        self._report("Competitors with no team", teamless_rows, lambda r: (
            f"{r['user'].username} <{r['user'].email}>"
            f"{' - side declared' if r['side_declared'] else ' - no side yet'}"
        ))

        if not short_rows and not teamless_rows:
            self.stdout.write(self.style.SUCCESS("Nothing to send."))
            return
        if not send:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Preview only. Re-run with --confirm to send."))
            return

        sent = 0
        with transaction.atomic():
            for row in short_rows:
                result = cat.roster_short(
                    row["user"], row["team"], row["seats"],
                    deadline=row["deadline"], is_captain=row["is_captain"],
                    recruit_url=f"{site}/accounts/recruit/",
                )
                sent += 1 if result is not None else 0
            for row in teamless_rows:
                result = cat.no_team_yet(
                    row["user"], deadline=row["deadline"],
                    side_declared=row["side_declared"],
                    find_team_url=f"{site}/accounts/find-team/",
                )
                sent += 1 if result is not None else 0

        self.stdout.write(self.style.SUCCESS(f"Sent {sent} message(s)."))

    def _report(self, title, rows, line):
        self.stdout.write("")
        self.stdout.write(f"{title}: {len(rows)}")
        for row in rows:
            self.stdout.write("  " + line(row))
