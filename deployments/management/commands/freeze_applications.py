"""Preview each blue team's last passing image before the range-side freeze.

    python manage.py freeze_applications --dry-run

The website cannot copy an image into the range registry or snapshot the
database. The actual freeze runs on Server 3 with
`python tools/deploy-agent/make-cells.py --freeze`.

This is a read-only candidate report. The Server 3 range runner performs
the registry copy, records each actual digest, and captures database seeds.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from competitions.models import Competition
from services import deployment_service as deploy


class Command(BaseCommand):
    help = "Preview each blue team's last passing application image."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int, help="Competition id; the open one by default.")
        parser.add_argument("--dry-run", action="store_true", help="Accepted for compatibility; this command is always read-only.")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Refuse legacy website-side freezing; run the Server 3 range command instead.",
        )

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))
        deadline = deploy.freeze_deadline(competition)
        self.stdout.write(f"{competition.name}")
        if deadline:
            self.stdout.write(
                f"Build deadline: {timezone.localtime(deadline):%d %b %Y %H:%M} EAT"
                f"{'' if deploy.freeze_passed(competition) else ' (not reached yet)'}"
            )
        else:
            self.stdout.write(self.style.WARNING(
                "No build deadline is set, so nothing enforces a freeze."
            ))

        counts = deploy.summary(competition)
        self.stdout.write(
            f"{counts['teams']} application team(s): {counts['passing']} passing, "
            f"{counts['submitted_not_passing']} submitted but not passing, "
            f"{counts['never_submitted']} never submitted."
        )

        if options["confirm"]:
            raise CommandError(
                "A website-only command cannot copy image digests or snapshot databases. "
                "Run `python tools/deploy-agent/make-cells.py --freeze` on Server 3."
            )

        result = deploy.freeze(competition)
        for submission in result["pinned"]:
            self.stdout.write(
                f"  candidate  {submission.team.cell_label or submission.team.team_name}: "
                f"{submission.image_reference}"
            )
        for team in result["missing"]:
            latest = deploy.latest_for_team(team)
            tail = (
                f"last attempt {latest.attempt} {latest.get_status_display().lower()}"
                if latest else "never submitted"
            )
            self.stdout.write(self.style.ERROR(
                f"  NO IMAGE {team.cell_label or team.team_name} "
                f"({team.captain.email}) - {tail}"
            ))

        self.stdout.write("")
        self.stdout.write(
            f"{len(result['pinned'])} image(s) ready for the Server 3 freeze."
        )
        if result["missing"]:
            raise SystemExit(
                f"{len(result['missing'])} team(s) have no application in the competition."
            )

    # ------------------------------------------------------------------

    def _competition(self, pk):
        if pk:
            try:
                return Competition.objects.get(pk=pk)
            except Competition.DoesNotExist:
                raise CommandError(f"No competition with id {pk}.")
        competition = (
            Competition.objects.filter(
                status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS)
            )
            .order_by("-start_date")
            .first()
        )
        if competition is None:
            raise CommandError("No open competition. Pass --competition <id>.")
        return competition
