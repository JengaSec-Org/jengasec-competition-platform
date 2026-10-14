"""Publish a policy version -- the code of conduct, or the Participant Terms.

    python manage.py seed_policy                          # conduct v1.0, built-in text
    python manage.py seed_policy --ver 1.1 --file conduct.md --summary "Clarified scope"
    python manage.py seed_policy --kind terms --ver 4.0         --file docs/participant-terms-v4.0.md         --title "JengaSec 2026 Participant Terms and Conditions"

Each kind has its own current version, so publishing the terms leaves the
code of conduct exactly as it was.

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
        parser.add_argument(
            "--kind", default=PolicyVersion.Kind.CONDUCT,
            choices=[k for k, _ in PolicyVersion.Kind.choices],
            help="Which document this is (default: conduct).",
        )

    def handle(self, *args, **options):
        kind = options["kind"]
        if kind == PolicyVersion.Kind.TERMS and not options["file"]:
            raise CommandError(
                "--file is required for the terms: there is no built-in text, and "
                "publishing an empty agreement would be worse than publishing none."
            )
        body = CONDUCT_V1
        if options["file"]:
            path = Path(options["file"])
            if not path.exists():
                raise CommandError(f"{path} not found")
            body = path.read_text(encoding="utf-8")
        title = options["title"]
        if kind == PolicyVersion.Kind.TERMS and title == "JengaSec Code of Conduct":
            title = "JengaSec 2026 Participant Terms and Conditions"
        version, created = PolicyVersion.objects.get_or_create(
            version=options["ver"], kind=kind,
            defaults={"title": title, "summary": options["summary"], "body": body},
        )
        if not created and version.body != body:
            # Re-running after editing the file should publish what the file
            # now says, not what it said the first time.
            version.body = body
            version.title = title
            version.summary = options["summary"] or version.summary
            version.save(update_fields=["body", "title", "summary"])
        if version.is_current:
            self.stdout.write(
                f"{version.get_kind_display()} v{version.version} is already current."
            )
            return
        account_service.publish_policy(version)
        self.stdout.write(self.style.SUCCESS(
            f"{'Created and published' if created else 'Published'} "
            f"{version.get_kind_display()} v{version.version}; "
            f"competitors have been notified to accept it."
        ))
