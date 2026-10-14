"""Create the published proposal rubrics.

    python manage.py seed_proposal_rubrics --dry-run
    python manage.py seed_proposal_rubrics --confirm

Until this runs, `seed_dev_data` leaves one empty rubric per proposal type
titled "(placeholder)" with no criteria at all — which means a judge opening
the review queue sees nothing to mark against, and the AI evaluator has no
rubric to score with.

The criteria are the Entry Guide's own sections and weights
(`competitions/proposal_rubric.py`), so a judge marking in the platform and
a team reading the Guide are looking at the same list.

Safe to run again: criteria are matched on title and updated in place, so
re-running after a wording change does not create a second set.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from competitions import proposal_rubric
from competitions.models import Competition
from rubrics.models import Rubric, RubricCriterion
from submissions.models import SubmissionType


class Command(BaseCommand):
    help = "Create the proposal rubrics from the Entry Guide."

    def add_arguments(self, parser):
        parser.add_argument("--competition", type=int)
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        competition = self._competition(options.get("competition"))
        self.stdout.write(f"{competition.name}")

        plan = []
        for type_name, kind in proposal_rubric.RUBRIC_FOR_TYPE.items():
            submission_type = SubmissionType.objects.filter(name=type_name).first()
            if submission_type is None:
                self.stdout.write(self.style.WARNING(
                    f"  skip   {type_name}: no such submission type"
                ))
                continue
            plan.append((submission_type, kind))

        if not plan:
            raise CommandError(
                "No proposal submission types exist. Run seed_dev_data first."
            )

        for submission_type, kind in plan:
            rows = proposal_rubric.sections(kind)
            weight = proposal_rubric.total_weight(kind)
            self.stdout.write(
                f"  {submission_type.name}: {len(rows)} criteria, total weight {weight}"
            )
            for number, title, section_weight, _ in rows:
                marker = f"{section_weight:>3}" if section_weight else "  -"
                self.stdout.write(f"      {number:>2}. {title:<36} {marker}")

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write("Nothing written. Re-run with --confirm.")
            return

        created = self._apply(competition, plan)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{created['rubrics']} rubric(s), {created['criteria']} criteria."
        ))
        self.stdout.write(
            "Judges see these under Rubrics; the AI evaluator scores against them."
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
            ).order_by("-start_date").first()
        )
        if competition is None:
            raise CommandError("No open competition. Pass --competition <id>.")
        return competition

    @transaction.atomic
    def _apply(self, competition, plan):
        rubrics = criteria = 0
        for submission_type, kind in plan:
            rubric, made = Rubric.objects.get_or_create(
                competition=competition,
                submission_type=submission_type,
                defaults={
                    "title": f"{submission_type.name} — Entry Guide v1.0",
                    "description": (
                        "Sections, order and weights as published in the JengaSec "
                        "Entry Guide v1.0. A missing section scores zero on its "
                        "criterion and is not inferred from elsewhere."
                    ),
                    "is_active": True,
                },
            )
            rubrics += 1 if made else 0
            # The placeholder seed leaves an empty rubric behind; adopt it
            # rather than leaving two.
            if not made and "placeholder" in (rubric.title or "").lower():
                rubric.title = f"{submission_type.name} — Entry Guide v1.0"
                rubric.is_active = True
                rubric.save(update_fields=["title", "is_active"])

            for number, title, weight, description in proposal_rubric.sections(kind):
                _, made_criterion = RubricCriterion.objects.update_or_create(
                    rubric=rubric,
                    criterion=title,
                    defaults={
                        "description": description,
                        "weight": weight,
                        "max_score": 100,
                        "display_order": number,
                    },
                )
                criteria += 1 if made_criterion else 0
        return {"rubrics": rubrics, "criteria": criteria}
