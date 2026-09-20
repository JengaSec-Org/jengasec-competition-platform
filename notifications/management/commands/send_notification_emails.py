"""Send the notifications that are owed an email.

    python manage.py send_notification_emails             # drain the queue
    python manage.py send_notification_emails --limit 50  # cap one run
    python manage.py send_notification_emails --dry-run   # count, send nothing

Run from cron every few minutes:

    */5 * * * * cd /srv/jengasec && .venv/bin/python manage.py send_notification_emails

Creating a notification never touches SMTP -- that happens here, off the
request cycle, so a slow or unreachable mail server cannot fail a
submission. This is the seam where a task queue (Celery) slots in later:
the same service call, moved onto a worker.
"""
from django.core.management.base import BaseCommand

from services import notification_service


class Command(BaseCommand):
    help = "Deliver queued notification emails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of emails to send in this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent without sending it.",
        )

    def handle(self, *args, **options):
        sent, failed, skipped = notification_service.send_pending_emails(
            limit=options["limit"], dry_run=options["dry_run"]
        )

        if options["dry_run"]:
            self.stdout.write(f"{skipped} notification(s) awaiting email.")
            return

        if sent:
            self.stdout.write(self.style.SUCCESS(f"Sent {sent} email(s)."))
        if failed:
            # Not a hard failure: these are retried on the next run until
            # MAX_SEND_ATTEMPTS retires them. Details are in the log.
            self.stdout.write(
                self.style.WARNING(f"{failed} email(s) failed and will be retried.")
            )
        if not sent and not failed:
            self.stdout.write("Nothing to send.")
