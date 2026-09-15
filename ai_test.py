# from django.utils import timezone
# from judging.models import Evaluation, CriterionScore
# from ai_engine.models import AIEvaluationRun, AICriterionResult
# from services.ai_service import evaluate_document

# # 1. Grab the evaluation to test (replace with the real pk)
# evaluation = Evaluation.objects.get(pk=1)

# # 2. Build the rubric dict evaluate_document() expects, from the REAL rubric/criteria
# rubric = evaluation.rubric
# rubric_dict = {
#     "title": rubric.title,
#     "criteria": [
#         {"criterion": c.criterion, "description": c.description, "max_score": float(c.max_score)}
#         for c in rubric.criteria.all()
#     ],
# }

# # 3. Placeholder document text -- real extraction is parser_service's job, not built yet.
# #    This just proves the AI call + database writes work end-to-end.
# document_text = """
# Our submission proposes an automated judging platform using local LLMs via Ollama.
# Executive summary: reduces judge workload by pre-scoring submissions against
# rubric criteria while keeping humans in final control of every score.
# """

# # 4. Run the real AI evaluation (this actually calls Ollama)
# results = evaluate_document(document_text, rubric_dict)
# print(results)  # sanity check before writing anything to the database

# # 5. Persist the immutable audit record
# run = AIEvaluationRun.objects.create(
#     evaluation=evaluation,
#     model_name="phi4-mini:latest",  # match whatever model you actually pulled
#     status=AIEvaluationRun.Status.SUCCEEDED,
#     raw_output=results,
#     completed_at=timezone.now(),
# )

# # criteria_by_name = {c.criterion: c for c in rubric.criteria.all()}
# criteria_by_name = {c.criterion.strip().lower(): c for c in rubric.criteria.all()}

# for r in results:
#     #criterion = criteria_by_name.get(r["criterion"])
#     criterion = criteria_by_name.get(r["criterion"].strip().lower())
#     if criterion is None:
#         print(f"WARNING: AI returned '{r['criterion']}' -- no exact match in rubric, skipped.")
#         continue
#     AICriterionResult.objects.create(
#         ai_run=run,
#         criterion=criterion,
#         score=r["score"],
#         confidence=r["confidence"],
#         evidence=r["evidence"],
#         explanation=r["reasoning"],  # field-name mismatch mapped here, as flagged earlier
#     )
#     CriterionScore.objects.update_or_create(
#         evaluation=evaluation,
#         criterion=criterion,
#         defaults={
#             "ai_score": r["score"],
#             "confidence": r["confidence"],
#             "evidence": r["evidence"],
#             "reasoning": r["reasoning"],
#         },
#     )

# # 6. Move past "pending" so Judge Review unlocks, and recompute the total
# evaluation.status = Evaluation.Status.AI_COMPLETE
# evaluation.save(update_fields=["status", "updated_at"])
# evaluation.recalculate_total()

# print("Done:", evaluation.status, "| weighted_total:", evaluation.weighted_total)


from django.utils import timezone
from pypdf import PdfReader

from judging.models import Evaluation, CriterionScore
from ai_engine.models import AIEvaluationRun, AICriterionResult
from services.ai_service import evaluate_document


def extract_pdf_text(file_field):
    """
    TEST-ONLY PDF text extractor, for proving the AI pipeline works against
    a real document. This is NOT services/parser_service.py's real
    implementation -- that still needs an owner, needs to handle non-PDF
    file types, and likely needs OCR for scanned documents. This function
    only exists to unblock testing the AI evaluation step.
    """
    file_field.open("rb")
    try:
        reader = PdfReader(file_field)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    finally:
        file_field.close()
    return text


# 1. Grab the evaluation to test
evaluation = Evaluation.objects.get(pk=1)  # replace with the real pk

# 2. Get the real uploaded file and extract its text
submission_file = evaluation.submission.latest_file
if submission_file is None:
    raise ValueError("No file uploaded for this submission -- nothing to extract.")

document_text = extract_pdf_text(submission_file.file)
print(f"Extracted {len(document_text)} characters. First 500:")
print(document_text[:500])

if not document_text.strip():
    print("WARNING: extracted text is empty -- likely a scanned/image-only PDF, which needs OCR (out of scope here).")

# 3. Build the rubric dict from the REAL rubric/criteria
rubric = evaluation.rubric
rubric_dict = {
    "title": rubric.title,
    "criteria": [
        {"criterion": c.criterion, "description": c.description, "max_score": float(c.max_score)}
        for c in rubric.criteria.all()
    ],
}

# 4. Run the real AI evaluation against the REAL extracted text
results = evaluate_document(document_text, rubric_dict)
print(results)

# 5. Persist the immutable audit record
run = AIEvaluationRun.objects.create(
    evaluation=evaluation,
    model_name="qwen3.5:4b",  # match whatever model you actually pulled
    status=AIEvaluationRun.Status.SUCCEEDED,
    raw_output=results,
    completed_at=timezone.now(),
)

criteria_by_name = {c.criterion.strip().lower(): c for c in rubric.criteria.all()}

for r in results:
    criterion = criteria_by_name.get(r["criterion"].strip().lower())
    if criterion is None:
        print(f"WARNING: AI returned '{r['criterion']}' -- no match in rubric, skipped.")
        continue



    raw_score = r["score"]
    clamped_score = max(0, min(raw_score, float(criterion.max_score)))
    if clamped_score != raw_score:
        print(f"WARNING: AI gave {raw_score} for '{criterion.criterion}' "
              f"(max {criterion.max_score}) -- clamped to {clamped_score}.")

        

    AICriterionResult.objects.create(
        ai_run=run,
        criterion=criterion,
        # score=r["score"],
        score=clamped_score,
        confidence=r["confidence"],
        evidence=r["evidence"],
        explanation=r["reasoning"],
    )

    CriterionScore.objects.update_or_create(
        evaluation=evaluation,
        criterion=criterion,
        defaults={
           # "ai_score": r["score"],
            "ai_score": clamped_score,
            "confidence": r["confidence"],
            "evidence": r["evidence"],
            "reasoning": r["reasoning"],
        },
    )

# 6. Move past "pending" so Judge Review unlocks, and recompute the total
evaluation.status = Evaluation.Status.AI_COMPLETE
evaluation.save(update_fields=["status", "updated_at"])
evaluation.recalculate_total()

print("Done:", evaluation.status, "| weighted_total:", evaluation.weighted_total)