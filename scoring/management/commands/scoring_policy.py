"""Inspect existing totals, freeze policy, or finalize automatic publication."""
import json

from django.core.management.base import BaseCommand, CommandError

from competitions.models import Competition
from scoring.enrollment import enroll_policy, finalize_policy, reconciliation_report
from scoring.protocol import timestamp


class Command(BaseCommand):
    help = "Preflight, enroll or finalize the versioned Main scoring policy"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("preflight", "enroll", "finalize"))
        parser.add_argument("--competition", type=int, required=True)
        parser.add_argument("--start")
        parser.add_argument("--end")
        parser.add_argument("--archive-budget-bytes", type=int)
        parser.add_argument("--reviewed-reconciliation")

    def handle(self, *args, **options):
        try:
            competition = Competition.objects.get(pk=options["competition"])
            if options["action"] == "preflight":
                report, sha256 = reconciliation_report(competition)
                self.stdout.write(json.dumps({"report": report, "sha256": sha256}, indent=2))
                return
            if options["action"] == "enroll":
                enrolled = enroll_policy(
                    competition, window_start=timestamp(options["start"], "start"),
                    window_end=timestamp(options["end"], "end"),
                    archive_budget_bytes=options["archive_budget_bytes"],
                    reviewed_reconciliation=options["reviewed_reconciliation"])
                self.stdout.write(f"Enrolled competition {competition.pk} in {enrolled.policy_version}.")
            else:
                finalize_policy(competition)
                self.stdout.write(f"Finalized automatic scoring for competition {competition.pk}.")
        except (ValueError, Competition.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
