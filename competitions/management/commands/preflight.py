"""Is this deployment ready to take real registrations?

    python manage.py preflight

Checks the things that are wrong *in production* rather than in the code:
settings that are still on their development defaults, storage that would
lose uploaded ID documents on the next deploy, an email path that silently
drops verification links, a competition with no deadline or no briefs.

Exit code is non-zero when anything is a BLOCKER, so it can gate a deploy.
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from accounts.models import PolicyVersion, Team
from competitions.models import ApplicationBrief, Competition

User = get_user_model()


class Command(BaseCommand):
    help = "Check that this deployment is ready to take real registrations."

    def handle(self, *args, **options):
        self.blockers = []
        self.warnings = []
        self.notes = []

        self.check_settings()
        self.check_database()
        self.check_storage()
        self.check_email()
        self.check_scheduler()
        self.check_competition()
        self.check_accounts()

        for note in self.notes:
            self.stdout.write(f"  ok      {note}")
        for warning in self.warnings:
            self.stdout.write(self.style.WARNING(f"  warn    {warning}"))
        for blocker in self.blockers:
            self.stdout.write(self.style.ERROR(f"  BLOCK   {blocker}"))

        self.stdout.write("")
        if self.blockers:
            self.stdout.write(self.style.ERROR(
                f"{len(self.blockers)} blocker(s), {len(self.warnings)} warning(s). Not ready."
            ))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(
            f"No blockers, {len(self.warnings)} warning(s). Ready to take registrations."
        ))

    # ------------------------------------------------------------------

    def check_settings(self):
        if settings.DEBUG:
            self.blockers.append("DEBUG is on — set DJANGO_DEBUG=False.")
        else:
            self.notes.append("DEBUG is off.")

        if "insecure" in settings.SECRET_KEY or len(settings.SECRET_KEY) < 50:
            self.blockers.append("SECRET_KEY is the development default — set DJANGO_SECRET_KEY.")
        else:
            self.notes.append("SECRET_KEY is set.")

        hosts = [h for h in settings.ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]
        if not hosts:
            self.blockers.append(
                "ALLOWED_HOSTS has no public hostname — Django will answer 400 for the domain."
            )
        else:
            self.notes.append(f"ALLOWED_HOSTS covers {', '.join(hosts)}.")

        site = getattr(settings, "SITE_URL", "")
        if not site or "localhost" in site:
            self.blockers.append(
                f"SITE_URL is {site or 'unset'} — every link in every email would point there. "
                f"Set DJANGO_SITE_URL to the public https:// address."
            )
        elif not site.startswith("https://"):
            self.warnings.append(f"SITE_URL is {site}; it should be https:// in production.")
        else:
            self.notes.append(f"SITE_URL is {site}.")

        if settings.SESSION_COOKIE_AGE > 60 * 60 * 4:
            self.warnings.append(
                f"Sessions last {settings.SESSION_COOKIE_AGE // 3600}h; an idle "
                f"dashboard on a shared machine stays open that long."
            )
        else:
            self.notes.append(f"Idle sessions end after {settings.SESSION_COOKIE_AGE // 60} minutes.")

    def check_database(self):
        engine = settings.DATABASES["default"]["ENGINE"]
        if "sqlite" in engine:
            self.blockers.append(
                "Database is SQLite — set the JENGASEC_DB_* / JENGASEC_DB_URL variables. "
                "On a host with an ephemeral disk the whole database disappears on deploy."
            )
        else:
            self.notes.append(f"Database engine is {engine.rsplit('.', 1)[-1]}.")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            self.notes.append("Database answers.")
        except Exception as exc:  # noqa: BLE001
            self.blockers.append(f"Database is unreachable: {exc}")

    def check_storage(self):
        backend = settings.STORAGES["default"]["BACKEND"]
        if "S3" in backend:
            self.notes.append("Uploads go to object storage.")
            return
        on_render = bool(os.environ.get("RENDER"))
        message = (
            "Uploads are on the local disk. Identity documents and proposals "
            "are lost on the next deploy — set S3_BUCKET and the other S3_* variables."
        )
        (self.blockers if on_render else self.warnings).append(message)

    def check_email(self):
        backend = settings.EMAIL_BACKEND
        brevo = "Brevo" in backend
        smtp = "smtp" in backend
        if not (brevo or smtp):
            self.blockers.append(
                f"EMAIL_BACKEND is {backend.rsplit('.', 1)[-1]} — verification links would "
                f"be printed to the log, never sent. Set BREVO_API_KEY (Render blocks SMTP)."
            )
            return
        if brevo and not getattr(settings, "BREVO_API_KEY", ""):
            self.blockers.append("Brevo backend selected but BREVO_API_KEY is empty.")
            return
        if smtp and not settings.EMAIL_HOST_PASSWORD:
            self.blockers.append("SMTP backend selected but no password is set.")
            return
        if smtp and os.environ.get("RENDER"):
            self.blockers.append(
                "SMTP backend on Render: outbound SMTP is blocked there. Use BREVO_API_KEY."
            )
            return
        self.notes.append(f"Email goes out through {'Brevo' if brevo else 'SMTP'}.")

    def check_scheduler(self):
        """Whether anything will ever run the reminder jobs.

        Worth its own check because the failure is silent: with no token
        the runner at /tasks/run/ answers 404, every scheduled job simply
        never runs, and the first anyone knows is that no competitor was
        reminded about a deadline that has already passed.
        """
        token = (getattr(settings, "CRON_TOKEN", "") or "").strip()
        if not token:
            self.warnings.append(
                "DJANGO_CRON_TOKEN is not set, so /tasks/run/ is disabled and no "
                "deadline or digest reminder will ever be sent. Set it here and as "
                "the JENGASEC_CRON_TOKEN repository secret, or run the jobs by hand "
                "from the notification console."
            )
            return
        if len(token) < 16:
            self.warnings.append(
                f"DJANGO_CRON_TOKEN is only {len(token)} characters; it guards an "
                f"endpoint that emails every competitor. Use at least 32."
            )
            return
        self.notes.append("Scheduled notification runner is enabled.")

    def check_competition(self):
        competitions = Competition.objects.filter(
            status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS)
        )
        if not competitions.exists():
            self.blockers.append(
                "No competition is open — create one and set its status to Open, "
                "or nobody can register."
            )
            return
        for competition in competitions:
            row = competition.settings
            if competition.phase != Competition.Phase.REGISTRATION:
                self.warnings.append(
                    f"{competition.name}: phase is {competition.get_phase_display()}, "
                    f"so registration is closed."
                )
            if not row.registration_deadline:
                self.warnings.append(
                    f"{competition.name}: no registration deadline — registration never closes "
                    f"by itself and the countdown emails cannot fire."
                )
            elif row.registration_deadline <= timezone.now():
                self.warnings.append(
                    f"{competition.name}: registration deadline already passed "
                    f"({timezone.localtime(row.registration_deadline):%d %b %Y %H:%M})."
                )
            else:
                self.notes.append(
                    f"{competition.name}: registration open until "
                    f"{timezone.localtime(row.registration_deadline):%d %b %Y %H:%M}."
                )
            briefs = ApplicationBrief.objects.filter(competition=competition, is_open=True).count()
            if briefs == 0:
                self.blockers.append(
                    f"{competition.name}: no application briefs — blue teams have nothing to "
                    f"propose against. Run seed_application_briefs --competition {competition.pk}."
                )
            else:
                self.notes.append(f"{competition.name}: {briefs} application brief(s) published.")

    def check_accounts(self):
        if not User.objects.filter(is_superuser=True).exists():
            self.blockers.append("No superuser — nobody can review registrations.")
        else:
            self.notes.append("At least one organiser account exists.")

        if PolicyVersion.current() is None:
            self.blockers.append(
                "No code of conduct published — every registration is blocked on accepting it. "
                "Run seed_policy --file conduct.md."
            )
        else:
            self.notes.append(f"Code of conduct v{PolicyVersion.current().version} is current.")

        seeded = User.objects.filter(email__endswith="@jengasec.test").count()
        if seeded:
            self.warnings.append(
                f"{seeded} development seed account(s) present (@jengasec.test). "
                f"Remove them before real teams register."
            )
        demo_teams = Team.objects.filter(team_name__in=("Nyati Defenders", "Simba Cell")).count()
        if demo_teams:
            self.warnings.append(f"{demo_teams} seeded demo team(s) still in the database.")
