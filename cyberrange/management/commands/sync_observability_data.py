"""Advance versioned attribution from current platform-owned records."""
from django.core.management.base import BaseCommand, CommandError

from competitions.models import Competition
from services.scoring_identity import IdentityError, refresh_identity


class Command(BaseCommand):
    help = "Refresh existing scoring identity history without inventing target or address assignments."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int, required=True)

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(pk=options["competition"])
            state = refresh_identity(competition)
        except (Competition.DoesNotExist, IdentityError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"Identity revision {state.revision} refreshed.")
