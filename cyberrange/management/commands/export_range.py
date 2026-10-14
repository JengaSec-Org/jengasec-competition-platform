"""Write the files the cyber range reads (AIC-10).

    python manage.py export_range --competition 1 --out range-export/
    python manage.py export_range --competition 1 --out range-export/ --fill-namespaces

Produces, in --out:
    cells.yaml              -> jengasec-cyberrange-infrastructure/scripts/cells.yaml
    scope-assignments.csv   -> input to scripts/make-allowlists.py (SEC-10)
    allocation.csv          -> input to scripts/create-accounts (APP-12)

Commit cells.yaml to the infra repo in a pull request. The legacy allocation
CSV carries team names, so share it with APP/SEC directly rather than
committing it. Observability consumes the privacy-safe database views instead.
"""
import pathlib

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.models import AuditLog
from competitions.models import Competition
from services import range_service
from services.audit_service import record as audit


class Command(BaseCommand):
    help = "Export cells.yaml, scope-assignments.csv and allocation.csv for the cyber range."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int, required=True)
        parser.add_argument("--out", required=True)
        parser.add_argument(
            "--fill-namespaces",
            action="store_true",
            help="Store the canonical namespace only on Blue teams whose namespace is empty.",
        )
        parser.add_argument(
            "--enable-optional",
            action="store_true",
            help="Enable APP08 and APP09 in both enterprises.",
        )

    def handle(self, *args, **opts):
        try:
            competition = Competition.objects.get(pk=opts["competition"])
        except Competition.DoesNotExist as exc:
            raise CommandError(f"No competition with id {opts['competition']}") from exc

        try:
            with transaction.atomic():
                if opts["fill_namespaces"]:
                    range_service.fill_namespaces(competition)
                result = range_service.sync_observability_data(
                    competition, enable_optional=opts["enable_optional"]
                )
        except range_service.ObservabilitySyncError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"reconciled {result['applications']} applications and "
            f"{result['scope_assignments']} scope assignments"
        )

        out = pathlib.Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        files = {
            "cells.yaml": range_service.cells_yaml(competition),
            "scope-assignments.csv": range_service.scope_csv(competition),
            "allocation.csv": range_service.allocation_csv(competition),
        }
        for name, content in files.items():
            (out / name).write_text(content, encoding="utf-8", newline="\n")
            self.stdout.write(f"wrote {out / name} ({content.count(chr(10))} lines)")

        audit(
            AuditLog.Action.RANGE_EXPORTED,
            target=competition,
            description=f"Range files exported to {out}",
        )
        self.stdout.write(self.style.SUCCESS("done"))
