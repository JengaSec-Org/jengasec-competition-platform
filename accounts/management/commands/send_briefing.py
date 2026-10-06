"""One email per competitor covering everything outstanding.

    python manage.py send_briefing                     # preview
    python manage.py send_briefing --confirm           # send
    python manage.py send_briefing --extra-red FILE    # plus a note to red teams only

Replaces sending four things in a day -- complete your details, re-upload
your ID, the new deadlines, the privacy notice. Four emails in a day is
how people learn to ignore mail from the organisers; one broadcast cannot
say what any particular person still owes, so everybody who has already
finished ignores it too.

So: one message per person, assembled from their own record.

Order matters here. The identity sweep runs first and *silently* -- it
clears the rows whose file is missing from storage, which is what makes
"upload your student or national ID" appear in that person's outstanding
list. Without that step the briefing would tell someone their account is
complete while their ID is a dead reference.
"""
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Team, TeamMember, UserProfile
from notifications.management.commands.send_outstanding_digest import (
    outstanding_for_member,
)
from services import competition_service, notification_catalogue as cat


class Command(BaseCommand):
    help = "Send each competitor a single email covering everything they still owe."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm", action="store_true",
            help="Send it. Without this, report who would get what.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Explicit no-op form.")
        parser.add_argument(
            "--slug", default="brief",
            help="Dedupe identity. Change it to send a second, different briefing.",
        )
        parser.add_argument(
            "--extra-red", default="",
            help="File with an extra paragraph for red teams only, e.g. the "
                 "partner challenges note.",
        )
        parser.add_argument(
            "--extra-blue", default="", help="The same, for blue teams only."
        )
        parser.add_argument(
            "--skip-identity-sweep", action="store_true",
            help="Do not clear ID rows missing from storage first. Only for a "
                 "rehearsal when the file store is unreachable.",
        )

    def handle(self, *args, **options):
        send = options["confirm"] and not options["dry_run"]
        extra_red = self._text(options["extra_red"])
        extra_blue = self._text(options["extra_blue"])

        # 1. Work out whose document is a dead reference BEFORE clearing any,
        #    because afterwards an emptied field is indistinguishable from one
        #    that was never filled -- and apologising for losing a file
        #    somebody never uploaded is worse than saying nothing.
        lost_user_ids = set()
        if not options["skip_identity_sweep"]:
            self.stdout.write("Reconciling identity documents with storage...")
            lost_user_ids = self._lost_documents()
            try:
                call_command(
                    "request_identity_reupload",
                    *(["--confirm", "--no-email"] if send else []),
                    stdout=self.stdout,
                )
            except Exception as exc:  # noqa: BLE001
                raise CommandError(
                    f"Could not reach the file store ({type(exc).__name__}: {exc}). "
                    f"Fix that first, or pass --skip-identity-sweep to brief anyway."
                )
            self.stdout.write("")

        members = (
            TeamMember.objects.exclude(user__isnull=True)
            .exclude(team__status=Team.Status.WITHDRAWN)
            .select_related("user", "user__profile", "team", "team__competition")
            .order_by("team__team_name", "user__username")
        )

        plan, unreachable = [], []
        for member in members:
            if not member.user.email:
                unreachable.append(member)
                continue
            plan.append({
                "member": member,
                "items": outstanding_for_member(member),
                "dates": competition_service.key_dates(member.team.competition, member.team),
                # Only for a document that existed and is gone -- never for one
                # that was never uploaded.
                "identity_lost": member.user_id in lost_user_ids,
                "extra": extra_red if member.team.team_type == Team.TeamType.RED else extra_blue,
            })

        self.stdout.write(f"Competitors to brief: {len(plan)}")
        if unreachable:
            self.stdout.write(self.style.WARNING(
                f"{len(unreachable)} have no email address and cannot be reached: "
                + ", ".join(m.user.username for m in unreachable)
            ))

        for row in plan:
            member = row["member"]
            flags = []
            if row["identity_lost"]:
                flags.append("needs ID again")
            if member.team.is_locked:
                flags.append("locked registration")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            self.stdout.write(
                f"  {member.user.username} <{member.user.email}> - "
                f"{member.team.team_name}: {len(row['items'])} outstanding{suffix}"
            )

        if not plan:
            self.stdout.write(self.style.SUCCESS("Nobody to brief."))
            return
        if not send:
            self.stdout.write("")
            self.stdout.write("--- sample, as the first recipient would read it " + "-" * 20)
            self.stdout.write(self._sample(plan[0], options["slug"]))
            self.stdout.write("-" * 69)
            self.stdout.write(self.style.WARNING("Preview only. Re-run with --confirm to send."))
            return

        sent = 0
        with transaction.atomic():
            for row in plan:
                result = cat.briefing(
                    row["member"].user,
                    items=row["items"],
                    dates=row["dates"],
                    team=row["member"].team,
                    identity_lost=row["identity_lost"],
                    extra=row["extra"],
                    slug=options["slug"],
                )
                if result is not None:
                    sent += 1

        self.stdout.write(self.style.SUCCESS(f"Briefed {sent} competitor(s)."))
        if sent < len(plan):
            self.stdout.write(
                f"{len(plan) - sent} had already had this briefing (slug "
                f"{options['slug']!r}); pass a different --slug to send a new one."
            )

    @staticmethod
    def _lost_documents():
        """Users whose stored ID reference points at nothing.

        Read before the sweep clears them: once the field is blank it
        looks exactly like a competitor who has not uploaded yet, and the
        briefing must not apologise to those people for losing a file
        they never sent.
        """
        from django.core.files.storage import default_storage

        lost = set()
        for profile in (
            UserProfile.objects.exclude(identity_document="").only(
                "user_id", "identity_document"
            )
        ):
            if not default_storage.exists(profile.identity_document.name):
                lost.add(profile.user_id)
        return lost

    def _sample(self, row, slug):
        """Render one briefing without creating it, for the preview."""
        from unittest.mock import patch

        captured = {}

        def fake(user, subject, body="", **kwargs):
            captured["subject"] = subject
            captured["body"] = body
            return None

        with patch.object(cat.ns, "notify_user", fake):
            cat.briefing(
                row["member"].user,
                items=row["items"], dates=row["dates"], team=row["member"].team,
                identity_lost=row["identity_lost"], extra=row["extra"], slug=slug,
            )
        return f"Subject: {captured.get('subject','')}\n\n{captured.get('body','')}"

    @staticmethod
    def _text(path_str):
        if not path_str:
            return ""
        path = Path(path_str)
        if not path.exists():
            raise CommandError(f"{path} not found")
        return path.read_text(encoding="utf-8").strip()
