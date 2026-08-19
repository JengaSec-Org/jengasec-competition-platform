"""Bulk-run the Document Processing Engine over uploaded files.

    python manage.py parse_submissions            # parse anything unparsed
    python manage.py parse_submissions --force    # re-parse everything
    python manage.py parse_submissions --failed   # retry failures only

This is the seam where a task queue (Celery) slots in later: the same
service call, moved off the request cycle.
"""
from django.core.management.base import BaseCommand

from services import submission_service
from submissions.models import ParsedDocument, SubmissionFile


class Command(BaseCommand):
    help = "Extract text and structure from uploaded submission documents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-parse files that already succeeded.",
        )
        parser.add_argument(
            "--failed",
            action="store_true",
            help="Only retry files whose last parse failed.",
        )

    def handle(self, *args, **options):
        files = SubmissionFile.objects.select_related("submission").order_by("pk")

        if options["failed"]:
            files = files.filter(parsed__status=ParsedDocument.Status.FAILED)
        elif not options["force"]:
            files = files.exclude(parsed__status=ParsedDocument.Status.SUCCEEDED)

        total = files.count()
        if not total:
            self.stdout.write("Nothing to parse.")
            return

        counts = {"succeeded": 0, "failed": 0, "skipped": 0}
        for submission_file in files:
            parsed = submission_service.parse_file(
                submission_file, force=options["force"]
            )
            counts[parsed.status] = counts.get(parsed.status, 0) + 1
            label = f"v{submission_file.version} {submission_file.filename}"
            if parsed.status == ParsedDocument.Status.SUCCEEDED:
                words = parsed.metadata.get("word_count", 0)
                self.stdout.write(self.style.SUCCESS(f"  OK   {label} ({words} words)"))
            elif parsed.status == ParsedDocument.Status.FAILED:
                self.stdout.write(
                    self.style.ERROR(f"  FAIL {label}: {parsed.error_message}")
                )
            else:
                self.stdout.write(f"  SKIP {label}")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nParsed {total} file(s): "
                f"{counts.get('succeeded', 0)} succeeded, "
                f"{counts.get('failed', 0)} failed, "
                f"{counts.get('skipped', 0)} skipped."
            )
        )
