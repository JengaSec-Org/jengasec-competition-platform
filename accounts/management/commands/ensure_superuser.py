"""Create the first admin account from environment variables, idempotently.

Hosts without a shell (Render's free tier) have no way to run
`createsuperuser` interactively, and the built-in `--noinput` variant errors
if the user already exists — which it will on every deploy after the first.

    DJANGO_SUPERUSER_USERNAME=admin
    DJANGO_SUPERUSER_EMAIL=admin@example.com
    DJANGO_SUPERUSER_PASSWORD=...

Does nothing when the variables are unset, so it is safe in every build.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the superuser named by DJANGO_SUPERUSER_* if it does not exist."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_USERNAME/PASSWORD not set; skipping.")
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists; nothing to do.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
