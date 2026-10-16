"""Run the AI evaluator over submitted proposals.

    python manage.py score_proposals --dry-run
    python manage.py score_proposals --confirm
    python manage.py score_proposals --confirm --team JS26-B-014

Creates one `Evaluation` per proposal and fills it with the model's
per-criterion scores, evidence and reasoning. **It awards nothing.** The
Entry mark follows when a judge approves the evaluation in the review
queue — `ai_service` is explicit that AI output is decision support, the
Entry Guide says proposals are judged "assisted by AI", and a model that
has never been calibrated against this rubric should not be setting a
team's mark on its own.

Needs Ollama reachable at `OLLAMA_BASE_URL` with `OLLAMA_MODEL` pulled,
and a rubric — run `seed_proposal_rubrics` first.

Safe to run again: an evaluation already approved by a judge is left
alone, so a re-run never overwrites a human decision.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from judging.models import CriterionScore, Evaluation
from rubrics.models import Rubric
from services import ai_service, judge_assignment_service
from submissions.models import ParsedDocument, Submission


class Command(BaseCommand):
    help = "Score submitted proposals with the local model, for judges to review."

    def add_arguments(self, parser):
        parser.add_argument("--team", help="One team identifier only.")
        parser.add_argument("--limit", type=int, help="Stop after this many.")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument(
            "--redo", action="store_true",
            help="Re-score evaluations the model has already done "
                 "(never those a judge has approved).",
        )

    def handle(self, *args, **options):
        work = self._queue(options)
        if not work:
            self.stdout.write("Nothing to score.")
            return

        self.stdout.write(f"{len(work)} proposal(s) to score:")
        for submission, rubric, _ in work:
            self.stdout.write(
                f"  {submission.team.team_name:<28} {submission.submission_type.name}"
                f"  ({rubric.criteria.count()} criteria)"
            )

        if not options["confirm"]:
            self.stdout.write("")
            self.stdout.write("Nothing scored. Re-run with --confirm.")
            return

        self._check_model()

        done = failed = 0
        for submission, rubric, text in work:
            label = submission.team.team_name
            try:
                self._score(submission, rubric, text)
            except Exception as exc:  # noqa: BLE001 — one bad document must not stop the run
                failed += 1
                self.stdout.write(self.style.ERROR(f"  FAIL  {label}: {exc}"))
                continue
            done += 1
            self.stdout.write(self.style.SUCCESS(f"  ok    {label}"))

        self.stdout.write("")
        self.stdout.write(
            f"{done} scored, {failed} failed. Nothing is marked yet — a judge "
            f"approves each one in the review queue, and the Entry mark follows."
        )

    # ------------------------------------------------------------------

    def _check_model(self):
        """Fail early and clearly rather than once per document."""
        from django.conf import settings

        try:
            from ollama import Client

            Client(host=settings.OLLAMA_BASE_URL).list()
        except Exception as exc:  # noqa: BLE001
            raise CommandError(
                f"Cannot reach Ollama at {settings.OLLAMA_BASE_URL}: {exc}\n"
                f"On the VM: systemctl status ollama, then "
                f"`ollama pull {settings.OLLAMA_MODEL}`."
            )

    def _queue(self, options):
        """(submission, rubric, text) for everything worth scoring."""
        submissions = (
            Submission.objects.filter(
                status__in=(Submission.Status.SUBMITTED, Submission.Status.UNDER_REVIEW)
            )
            .select_related("team", "submission_type", "competition")
            .order_by("team__team_name")
        )
        if options.get("team"):
            submissions = submissions.filter(
                team__team_identifier__iexact=options["team"]
            )

        work = []
        for submission in submissions:
            rubric = Rubric.objects.filter(
                competition=submission.competition,
                submission_type=submission.submission_type,
                is_active=True,
            ).prefetch_related("criteria").first()
            if rubric is None or not rubric.criteria.exists():
                continue  # not a proposal type, or no rubric published

            existing = Evaluation.objects.filter(
                submission=submission, rubric=rubric, judge=None
            ).first()
            if existing and existing.status in Evaluation.DONE_STATUSES:
                continue  # a judge has decided; leave it alone
            if existing and not options["redo"]:
                continue

            text = self._text_for(submission)
            if not text:
                self.stdout.write(self.style.WARNING(
                    f"  skip  {submission.team.team_name}: no readable text. "
                    f"Run parse_submissions first."
                ))
                continue
            work.append((submission, rubric, text))
            if options.get("limit") and len(work) >= options["limit"]:
                break
        return work

    def _text_for(self, submission):
        """The extracted text of the latest successfully parsed version."""
        parsed = (
            ParsedDocument.objects.filter(
                submission_file__submission=submission,
                status=ParsedDocument.Status.SUCCEEDED,
            )
            .order_by("-submission_file__version")
            .first()
        )
        return (parsed.text or "").strip() if parsed else ""

    @transaction.atomic
    def _score(self, submission, rubric, text):
        criteria = list(rubric.criteria.all())
        payload = {
            "title": rubric.title,
            "criteria": [
                {
                    "criterion": c.criterion,
                    "max_score": float(c.max_score),
                    "description": c.description,
                }
                for c in criteria
            ],
        }
        results = ai_service.evaluate_document(text, payload)
        by_name = {r["criterion"].strip().lower(): r for r in results}

        evaluation, _ = Evaluation.objects.get_or_create(
            submission=submission, rubric=rubric, judge=None,
            defaults={"started_at": timezone.now()},
        )
        for criterion in criteria:
            result = by_name.get(criterion.criterion.strip().lower())
            if result is None:
                # The model skipped a section. Recorded as unscored rather
                # than zero: a judge must decide whether it is missing from
                # the document or missing from the model's answer.
                CriterionScore.objects.update_or_create(
                    evaluation=evaluation, criterion=criterion,
                    defaults={"ai_score": None, "reasoning":
                              "The model returned nothing for this section."},
                )
                continue
            score = max(0.0, min(float(result["score"]), float(criterion.max_score)))
            CriterionScore.objects.update_or_create(
                evaluation=evaluation, criterion=criterion,
                defaults={
                    "ai_score": score,
                    "final_score": score,
                    "confidence": result.get("confidence"),
                    "evidence": (result.get("evidence") or "")[:4000],
                    "reasoning": (result.get("reasoning") or "")[:4000],
                },
            )

        evaluation.status = Evaluation.Status.AI_COMPLETE
        evaluation.completed_at = timezone.now()
        evaluation.save(update_fields=["status", "completed_at", "updated_at"])
        evaluation.recalculate_total()
        # Judges assigned before the model ran get its suggestions too.
        judge_assignment_service.share_ai_scores(submission, rubric)
        return evaluation
