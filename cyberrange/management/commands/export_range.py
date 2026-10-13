"""Write the files the cyber range reads (AIC-10).

    python manage.py export_range --competition 1 --out range-export/
    python manage.py export_range --competition 1 --out range-export/ --fill-namespaces

Produces, in --out:
    cells.yaml              -> jengasec-cyberrange-infrastructure/scripts/cells.yaml
    scope-assignments.csv   -> input to scripts/make-allowlists.py (SEC-10)
    allocation.csv          -> input to scripts/create-accounts (APP-12)

Commit cells.yaml to the infra repo in a pull request. The CSVs carry team
names, so share them with APP/SEC directly rather than committing them.
"""
import pathlib

from django.core.management.base import BaseCommand, CommandError

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
            help="Also store the canonical namespace (e.g. jengasec-ent-a-app03) on Blue teams that have none.",
        )

    def handle(self, *args, **opts):
        try:
            competition = Competition.objects.get(pk=opts["competition"])
        except Competition.DoesNotExist as exc:
            raise CommandError(f"No competition with id {opts['competition']}") from exc

        if opts["fill_namespaces"]:
            n = range_service.fill_namespaces(competition)
            self.stdout.write(f"stored namespaces on {n} team(s)")

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
