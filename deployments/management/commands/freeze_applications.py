"""Pin each blue team's last passing image at the build deadline.

    python manage.py freeze_applications --dry-run
    python manage.py freeze_applications --confirm

The freeze is the moment the Guide describes: "we lock your last passing
(Healthy) image by its exact version. After that, nothing you push to
your own registry changes what runs in the competition."

The output that matters is the second list. A team with no passing image
at the freeze has no application in the competition, and somebody has to
ring them -- so this prints them by cell, with what they did submit, and
exits non-zero when there are any, so a wrapper notices.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from audit.models import AuditLog
from competitions.models import Competition
from services import deployment_service as deploy
from services.audit_service import record as audit


class Command(BaseCommand):
    help = "Pin each blue team's last passing application image."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int, help="Competition id; the open one by default.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would be pinned.")
        parser.add_argument("--confirm", action="store_true", help="Actually pin them.")

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

        if not options["confirm"]:
            self._preview(competition)
            self.stdout.write("")
            self.stdout.write("Nothing was pinned. Re-run with --confirm.")
            return

        result = deploy.freeze(competition)
        for submission in result["pinned"]:
            self.stdout.write(
                f"  pinned  {submission.team.cell_label or submission.team.team_name}: "
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

        audit(
            AuditLog.Action.APP_FROZEN,
            description=(
                f"{competition.name}: froze {len(result['pinned'])} image(s), "
                f"{len(result['missing'])} team(s) with none"
            ),
            competition=competition.pk,
        )
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Froze {len(result['pinned'])} image(s)."))
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

    def _preview(self, competition):
        from accounts.models import Team
        from competitions.constants import Track

        teams = (
            Team.objects.filter(
                competition=competition,
                team_type=Team.TeamType.BLUE,
                track=Track.APPLICATION,
            )
            .exclude(status=Team.Status.WITHDRAWN)
            .select_related("captain")
            .order_by("enterprise", "cell_id")
        )
        for team in teams:
            passing = deploy.last_passing(team)
            label = team.cell_label or team.team_name
            if passing:
                self.stdout.write(f"  would pin  {label}: {passing.image_reference}")
            else:
                latest = deploy.latest_for_team(team)
                tail = (
                    f"last attempt {latest.attempt} {latest.get_status_display().lower()}"
                    if latest else "never submitted"
                )
                self.stdout.write(self.style.WARNING(
                    f"  NO IMAGE   {label} ({team.captain.email}) - {tail}"
                ))
