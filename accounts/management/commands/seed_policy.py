"""Publish a code-of-conduct version (Guide section 6, step 4).

    python manage.py seed_policy                          # v1.0 with the built-in text
    python manage.py seed_policy --ver 1.1 --file conduct.md --summary "Clarified scope"

Safe in production (unlike seed_dev_data). Publishing makes the version
current and notifies every competitor to re-accept; re-running for a
version that is already current changes nothing.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts.management.commands.seed_dev_data import CONDUCT_V1
from accounts.models import PolicyVersion
from services import account_service


class Command(BaseCommand):
    help = "Create and publish a code-of-conduct version."

    def add_arguments(self, parser):
        parser.add_argument("--ver", default="1.0", help="Version label, e.g. 1.1")
        parser.add_argument("--file", help="Text/Markdown file with the full policy body.")
        parser.add_argument("--summary", default="", help="What changed (goes into the notice).")
        parser.add_argument("--title", default="JengaSec Code of Conduct")

    def handle(self, *args, **options):
        body = CONDUCT_V1
        if options["file"]:
            path = Path(options["file"])
            if not path.exists():
                raise CommandError(f"{path} not found")
            body = path.read_text(encoding="utf-8")
        version, created = PolicyVersion.objects.get_or_create(
            version=options["ver"],
            defaults={"title": options["title"], "summary": options["summary"], "body": body},
        )
        if version.is_current:
            self.stdout.write(f"v{version.version} is already the current policy; nothing to do.")
            return
        account_service.publish_policy(version)
        self.stdout.write(self.style.SUCCESS(
            f"{'Created and published' if created else 'Published'} v{version.version}; "
            f"competitors have been notified to accept it."
        ))
