"""Diagnose outbound email: what is configured, whether SMTP accepts the
credentials, and what is stuck in the queue.

    python manage.py check_email                     # report only
    python manage.py check_email --send-to you@x.com # also send one test mail
    python manage.py check_email --drain             # send whatever is queued now

Run it in the Render shell when "emails are not going out". It never
prints the password.
"""
import os

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from notifications.models import Notification
from services import notification_service


class Command(BaseCommand):
    help = "Report the effective email configuration, test SMTP, show the queue."

    def add_arguments(self, parser):
        parser.add_argument("--send-to", help="Address to send a one-line test email to.")
        parser.add_argument("--drain", action="store_true", help="Send all queued notification emails now.")

    def handle(self, *args, **options):
        backend = settings.EMAIL_BACKEND
        self.stdout.write("Effective configuration")
        self.stdout.write(f"  EMAIL_BACKEND        = {backend}")
        self.stdout.write(f"  EMAIL_HOST           = {settings.EMAIL_HOST}:{settings.EMAIL_PORT} (TLS {settings.EMAIL_USE_TLS})")
        self.stdout.write(f"  EMAIL_HOST_USER      = {settings.EMAIL_HOST_USER}")
        pw = settings.EMAIL_HOST_PASSWORD or ""
        self.stdout.write(f"  EMAIL_HOST_PASSWORD  = {'set (' + str(len(pw)) + ' chars)' if pw else 'NOT SET'}")
        self.stdout.write(f"  DEFAULT_FROM_EMAIL   = {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"  SITE_URL             = {settings.SITE_URL}")
        self.stdout.write(f"  deliver on commit    = {getattr(settings, 'NOTIFICATION_EMAIL_ON_COMMIT', True)}")

        brevo = "Brevo" in backend
        self.stdout.write(f"  BREVO_API_KEY        = {'set' if getattr(settings, 'BREVO_API_KEY', '') else 'not set'}")

        problems = []
        if "smtp" not in backend and not brevo:
            problems.append(
                "EMAIL_BACKEND is neither SMTP nor Brevo: mail is printed to the server log, "
                "never sent. On Render set BREVO_API_KEY (SMTP ports are blocked there); "
                "elsewhere set DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend."
            )
        if "smtp" in backend and not pw:
            problems.append("DJANGO_EMAIL_HOST_PASSWORD is empty: Gmail will refuse the login.")
        if "smtp" in backend and os.environ.get("RENDER"):
            problems.append("This is a Render instance: outbound SMTP is blocked here, so the SMTP "
                            "backend cannot work. Use BREVO_API_KEY instead.")
        if "smtp" in backend and " " in pw:
            problems.append("The app password contains spaces; Gmail accepts it either way, but "
                            "some hosts strip or quote them -- paste the 16 characters without spaces.")
        if "localhost" in settings.SITE_URL and not settings.DEBUG:
            problems.append("SITE_URL still points at localhost: links inside emails will be dead. "
                            "Set DJANGO_SITE_URL to the public https:// address.")

        if "smtp" in backend:
            self.stdout.write("\nSMTP login test")
            try:
                conn = get_connection(fail_silently=False)
                conn.open()
                conn.close()
                self.stdout.write(self.style.SUCCESS("  OK: the server accepted the credentials."))
            except OSError as exc:
                problems.append(
                    f"Cannot reach {settings.EMAIL_HOST}:{settings.EMAIL_PORT} ({exc}). The host "
                    f"blocks outbound SMTP -- use the Brevo backend (BREVO_API_KEY)."
                )
            except Exception as exc:  # noqa: BLE001 - report whatever the server said
                problems.append(f"SMTP login failed: {exc}")
        elif brevo:
            self.stdout.write("\nBrevo: HTTPS delivery; use --send-to to prove the key and sender.")

        if options["send_to"]:
            self.stdout.write(f"\nSending a test email to {options['send_to']}")
            try:
                sent = send_mail(
                    "JengaSec email test",
                    f"Sent {timezone.now():%d %b %Y %H:%M} from {settings.SITE_URL} via {backend}.",
                    None,
                    [options["send_to"]],
                    fail_silently=False,
                )
                self.stdout.write(self.style.SUCCESS(f"  send_mail returned {sent}"))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"Test send failed: {exc}")

        queue = notification_service.pending_email_queryset()
        stuck = Notification.objects.filter(
            send_email=True, sent_at__isnull=True,
            send_attempts__gte=notification_service.MAX_SEND_ATTEMPTS,
        ).count()
        self.stdout.write("\nQueue")
        self.stdout.write(f"  waiting to send      = {queue.count()}")
        self.stdout.write(f"  retired after {notification_service.MAX_SEND_ATTEMPTS} fails = {stuck}")
        last = Notification.objects.filter(sent_at__isnull=False).order_by("-sent_at").first()
        self.stdout.write(f"  last successful send = {last.sent_at:%d %b %Y %H:%M} to {last.user.email}" if last else "  last successful send = never")

        if options["drain"]:
            sent, failed, _ = notification_service.send_pending_emails()
            self.stdout.write(f"  drained: {sent} sent, {failed} failed (see the log for the reason)")

        self.stdout.write("")
        if problems:
            for p in problems:
                self.stdout.write(self.style.ERROR(f"PROBLEM: {p}"))
        else:
            self.stdout.write(self.style.SUCCESS("No configuration problems found."))
