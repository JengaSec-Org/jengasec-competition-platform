"""Ask every competitor with gaps in their record to finish filling it in.

    python manage.py send_data_completion_notice             # preview
    python manage.py send_data_completion_notice --confirm   # send

Deliberately not scoped to teams still assembling. A submitted or
approved registration is locked, but the *person* record behind it is
not (`accounts.views.profile` has no lock guard), and campus access on
the day depends on it being complete -- so members of locked teams are
asked as well. The daily digest skips them because the checklist only
chases registrations it can still change.

The outstanding list is `outstanding_for_member`, the same function the
digest, the profile page and `registration_blockers` use, so nobody is
told to fix something the platform would not also flag.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Team, TeamMember
from notifications.management.commands.send_outstanding_digest import (
    outstanding_for_member,
)
from services import notification_catalogue as cat


class Command(BaseCommand):
    help = "Email competitors whose profile details are still incomplete."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Send the mail. Without it, only report who would get it.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Explicit no-op form, for the scheduled runner.",
        )

    def handle(self, *args, **options):
        send = options["confirm"] and not options["dry_run"]

        members = (
            TeamMember.objects.exclude(user__isnull=True)
            .exclude(team__status=Team.Status.WITHDRAWN)
            .select_related("user", "user__profile", "team", "team__competition")
            .order_by("team__team_name", "user__username")
        )

        pending, complete, unreachable = [], 0, []
        for member in members:
            items = outstanding_for_member(member)
            if not items:
                complete += 1
                continue
            if not member.user.email:
                unreachable.append(member)
                continue
            pending.append((member, items))

        self.stdout.write(
            f"Competitors: {complete + len(pending) + len(unreachable)}   "
            f"complete: {complete}   incomplete: {len(pending)}"
        )
        if unreachable:
            self.stdout.write(self.style.WARNING(
                f"{len(unreachable)} incomplete competitor(s) have no email address "
                f"and cannot be told: "
                + ", ".join(m.user.username for m in unreachable)
            ))
        if not pending:
            self.stdout.write(self.style.SUCCESS("Nothing to send."))
            return

        for member, items in pending:
            locked = " [locked registration]" if member.team.is_locked else ""
            self.stdout.write(
                f"  {member.user.username} <{member.user.email}> "
                f"- {member.team.team_name}{locked}: {len(items)} item(s)"
            )
            for item in items:
                self.stdout.write(f"      {item}")

        if not send:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Preview only. Re-run with --confirm to send."
            ))
            return

        sent = 0
        with transaction.atomic():
            for member, items in pending:
                if cat.complete_your_details(member.user, items) is not None:
                    sent += 1

        self.stdout.write(self.style.SUCCESS(f"Requested details from {sent} competitor(s)."))
        if sent < len(pending):
            self.stdout.write(
                f"{len(pending) - sent} had already been asked; the request is sent once "
                f"per person."
            )
