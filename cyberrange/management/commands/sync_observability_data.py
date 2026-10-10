"""Reconcile the versioned, privacy-safe observability interface."""

from django.core.management.base import BaseCommand, CommandError

from competitions.models import Competition
from services import range_service


class Command(BaseCommand):
    help = "Reconcile applications, IP allocations and scope assignments for Observability."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int, required=True)
        parser.add_argument(
            "--enable-optional",
            action="store_true",
            help="Enable APP08 and APP09 in both enterprises.",
        )

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(pk=options["competition"])
        except Competition.DoesNotExist as exc:
            raise CommandError(f"No competition with id {options['competition']}") from exc
        try:
            result = range_service.sync_observability_data(
                competition, enable_optional=options["enable_optional"]
            )
        except range_service.ObservabilitySyncError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "reconciled " + ", ".join(f"{key}={value}" for key, value in result.items())
            )
        )
