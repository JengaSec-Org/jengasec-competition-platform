"""AI evaluation service (Ollama).

Pipeline: parsed document -> prompt builder -> LLM -> per-criterion
evaluation {score, evidence, reasoning, confidence}.

AI output is decision support only — every evaluation must pass through
human judge review (judging module) before scores are final.
"""
from typing import List

from django.conf import settings
from ollama import Client
from pydantic import BaseModel, Field


class CriterionEvaluation(BaseModel):
    criterion: str
    score: float
    evidence: str
    reasoning: str
    confidence: float = Field(ge=0, le=1)


class EvaluationResult(BaseModel):
    results: List[CriterionEvaluation]


def build_prompt(document_text: str, rubric: dict) -> str:
    """Compose the evaluation prompt for one document against one rubric."""
    criteria_lines = [
        f"- {c['criterion']} (max {c.get('max_score', 100)} pts): {c.get('description', '')}"
        for c in rubric.get("criteria", [])
    ]
    criteria_block = "\n".join(criteria_lines)

    return f"""You are scoring a competition submission against a rubric.

RUBRIC: {rubric.get('title', 'Untitled Rubric')}
CRITERIA:
{criteria_block}

SUBMISSION TEXT:
\"\"\"
{document_text}
\"\"\"

For EACH criterion above, provide:
- criterion: the exact criterion name as listed
- score: a number from 0 up to that criterion's max points
- evidence: a direct quote or specific detail from the submission supporting your score
- reasoning: why you assigned this score
- confidence: your confidence in this score, from 0.0 (guessing) to 1.0 (certain)

Base your evidence and reasoning ONLY on the submission text above.
Do not invent details that are not present in the text."""


def evaluate_document(document_text: str, rubric: dict) -> list[dict]:
    """Run the LLM evaluation and return one result dict per criterion."""
    client = Client(host=settings.OLLAMA_BASE_URL)
    prompt = build_prompt(document_text, rubric)

    response = client.chat(
        model=settings.OLLAMA_MODEL,
        messages=[{"role": "user", "content": prompt}],
        format=EvaluationResult.model_json_schema(),

        think=False,

        options={"temperature": 0.1},
    )

    parsed = EvaluationResult.model_validate_json(response["message"]["content"])
    return [item.model_dump() for item in parsed.results]