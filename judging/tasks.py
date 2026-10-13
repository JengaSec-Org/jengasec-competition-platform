from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from pypdf import PdfReader

from services.ai_service import evaluate_document
from ai_engine.models import AIEvaluationRun, AICriterionResult
from .models import Evaluation, CriterionScore

from services.parser_service import parse_document


def extract_pdf_text(file_field):
    """
    STOPGAP text extractor, PDF-only. Not parser_service.py's real
    implementation (Pair 1's, not built yet).
    """
    file_field.open("rb")
    try:
        reader = PdfReader(file_field)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    finally:
        file_field.close()
    return text


def run_ai_evaluation_task(evaluation_id):
    """
    The actual AI evaluation work, decoupled from any HTTP request so it can
    run as a queued background task (via Django-Q2) instead of blocking
    whatever triggered it. Same logic proven out in ai_test.py and the
    original synchronous view.
    """
    try:
        evaluation = Evaluation.objects.select_related(
            "submission", "rubric"
        ).get(pk=evaluation_id)
    except Evaluation.DoesNotExist:
        return f"Evaluation {evaluation_id} no longer exists -- skipped."

    submission_file = evaluation.submission.latest_file
    if submission_file is None:
        return f"Evaluation {evaluation_id}: no file uploaded -- skipped."


    parsed = parse_document(submission_file.file.path)
    document_text = parsed["text"]

    
    if not document_text.strip():
        return f"Evaluation {evaluation_id}: no extractable text (scanned/image PDF?) -- skipped."

    rubric = evaluation.rubric
    rubric_dict = {
        "title": rubric.title,
        "criteria": [
            {"criterion": c.criterion, "description": c.description, "max_score": float(c.max_score)}
            for c in rubric.criteria.all()
        ],
    }

    try:
        results = evaluate_document(document_text, rubric_dict)
    except Exception as exc:
        return f"Evaluation {evaluation_id}: AI call failed -- {exc}"

    run = AIEvaluationRun.objects.create(
        evaluation=evaluation,
        model_name=settings.OLLAMA_MODEL,
        status=AIEvaluationRun.Status.SUCCEEDED,
        raw_output=results,
        completed_at=timezone.now(),
    )

    criteria_by_name = {c.criterion.strip().lower(): c for c in rubric.criteria.all()}
    saved_count = 0

    for r in results:
        criterion = criteria_by_name.get(r["criterion"].strip().lower())
        if criterion is None:
            continue

        clamped_score = max(0, min(r["score"], float(criterion.max_score)))

        AICriterionResult.objects.create(
            ai_run=run,
            criterion=criterion,
            score=clamped_score,
            confidence=r["confidence"],
            evidence=r["evidence"],
            explanation=r["reasoning"],
        )
        CriterionScore.objects.update_or_create(
            evaluation=evaluation,
            criterion=criterion,
            defaults={
                "ai_score": clamped_score,
                "confidence": r["confidence"],
                "evidence": r["evidence"],
                "reasoning": r["reasoning"],
            },
        )
        saved_count += 1

    evaluation.status = Evaluation.Status.AI_COMPLETE
    evaluation.save(update_fields=["status", "updated_at"])
    evaluation.recalculate_total()

    return f"Evaluation {evaluation_id}: {saved_count} criteria scored."