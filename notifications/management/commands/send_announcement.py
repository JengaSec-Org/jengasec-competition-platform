"""An organiser's own message to one side of the competition.

    python manage.py send_announcement --side red --subject "..." --file notice.md
    python manage.py send_announcement --side red --subject "..." --file notice.md --confirm --pin

Everything else in `notification_catalogue` announces something the
platform noticed. This is the case where a human has something to say
and needs it to reach teams who are not logged in.

`--file` rather than an inline string on purpose: announcement wording
goes out to everyone at once and cannot be recalled, so it belongs in a
file somebody can read before it is sent.

`--pin` also creates an `Announcement` row, which puts the same text on
the matching dashboards until an organiser takes it down. An email is
read once; something a team must see every time they open the platform
has to be on the page.

The work itself is in `services.announcement_service`, shared with the
staff console at `notifications:console`.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from services import announcement_service as svc


class Command(BaseCommand):
    help = "Email and optionally pin an announcement to one side of the competition."

    def add_arguments(self, parser):
        parser.add_argument("--side", choices=sorted(svc.SIDES), default="all")
        parser.add_argument("--subject", required=True)
        parser.add_argument("--file", required=True, help="Text/Markdown file with the body.")
        parser.add_argument(
            "--slug",
            default="",
            help="Dedupe identity. Defaults to a slug of the subject; change it to "
                 "deliberately re-send a revised version of the same notice.",
        )
        parser.add_argument(
            "--pin", action="store_true",
            help="Also show it on the matching dashboards until taken down.",
        )
        parser.add_argument(
            "--expires",
            default="",
            help="ISO date/time after which a pinned notice stops showing, "
                 "e.g. 2026-10-08T13:00.",
        )
        parser.add_argument(
            "--confirm", action="store_true",
            help="Send it. Without this, print the recipients and the body only.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Explicit no-op form.")

    def handle(self, *args, **options):
        body = self._body(options["file"])
        expires = self._expires(options["expires"])
        send = options["confirm"] and not options["dry_run"]

        try:
            plan = svc.preview(options["side"], options["subject"], body)
        except svc.AnnouncementError as exc:
            raise CommandError(str(exc))

        self.stdout.write(f"Competition : {plan['competition'].name}")
        self.stdout.write(
            f"Audience    : {options['side']} -- {plan['team_count']} team(s), "
            f"{plan['recipient_count']} member(s)"
        )
        self.stdout.write(f"Subject     : {plan['subject']}")
        self.stdout.write(f"Dedupe slug : {options['slug'].strip() or plan['slug']}")
        self.stdout.write(
            f"Pinned      : {'yes' if options['pin'] else 'no'}"
            + (f" until {timezone.localtime(expires):%d %b %Y %H:%M}" if expires else "")
        )
        self.stdout.write("")
        self.stdout.write("--- body " + "-" * 60)
        self.stdout.write(plan["body"])
        self.stdout.write("-" * 69)

        if not plan["team_count"]:
            self.stdout.write(self.style.WARNING("No teams match; nothing to send."))
            return
        if not send:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Preview only. Re-run with --confirm to send."))
            return

        try:
            result = svc.announce(
                options["side"],
                options["subject"],
                body,
                slug=options["slug"],
                pin=options["pin"],
                expires_at=expires,
            )
        except svc.AnnouncementError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            f"Announced to {result['reached']} recipient(s)"
            + (" and pinned to their dashboards." if result["pinned"] else ".")
        ))

    @staticmethod
    def _body(path_str):
        path = Path(path_str)
        if not path.exists():
            raise CommandError(f"{path} not found")
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            raise CommandError(f"{path} is empty")
        return body

    @staticmethod
    def _expires(raw):
        if not raw:
            return None
        try:
            parsed = timezone.datetime.fromisoformat(raw)
        except ValueError:
            raise CommandError(f"--expires {raw!r} is not an ISO date/time.")
        if timezone.is_naive(parsed):
            # Typed by a person, so read it on the competition's own clock.
            parsed = timezone.make_aware(parsed)
        return parsed
