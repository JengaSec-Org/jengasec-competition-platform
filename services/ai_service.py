"""AI evaluation service (Ollama).

Pipeline: parsed document -> prompt builder -> LLM -> per-criterion
evaluation {criterion, score, evidence, reasoning, confidence}.

AI output is decision support only — every evaluation must pass through
human judge review (judging module) before scores are final. If Ollama
is unreachable or returns garbage, AIEvaluationError is raised; callers
should leave ai_score NULL and let judges score manually.
"""
import json

import requests
from django.conf import settings


class AIEvaluationError(Exception):
    """Raised when the LLM cannot produce a usable evaluation."""


def rubric_to_dict(rubric):
    """Bridge a rubrics.Rubric model instance into the prompt format."""
    return {
        "title": rubric.title,
        "description": rubric.description,
        "criteria": [
            {
                "criterion": c.criterion,
                "description": c.description,
                "weight": float(c.weight),
                "max_score": float(c.max_score),
            }
            for c in rubric.criteria.all()
        ],
    }


def build_prompt(document_text: str, rubric: dict) -> str:
    """Compose a strict-JSON evaluation prompt for one document."""
    criteria_lines = "\n".join(
        f'- "{c["criterion"]}" (max score {c["max_score"]}, weight {c["weight"]}%):'
        f' {c.get("description", "")}'
        for c in rubric["criteria"]
    )
    return f"""You are an evaluation assistant for a cybersecurity competition.
Score the document below against each rubric criterion.

RUBRIC: {rubric["title"]}
CRITERIA:
{criteria_lines}

Respond with ONLY a JSON object of this exact shape (no prose):
{{"evaluations": [{{"criterion": "<name exactly as given>", "score": <number within 0..max>,
"evidence": "<short quote or reference from the document>",
"reasoning": "<2-3 sentence justification>",
"confidence": <0.0-1.0>}}]}}

DOCUMENT:
{document_text}"""


def evaluate_document(document_text: str, rubric: dict, model: str | None = None, timeout: int = 180) -> list[dict]:
    """Run the LLM evaluation. Returns one result dict per criterion."""
    prompt = build_prompt(document_text, rubric)
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model or settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AIEvaluationError(f"Ollama request failed: {exc}") from exc

    try:
        payload = json.loads(response.json()["response"])
        results = payload["evaluations"]
    except (KeyError, ValueError, TypeError) as exc:
        raise AIEvaluationError(f"Could not parse model output: {exc}") from exc

    required = {"criterion", "score", "evidence", "reasoning", "confidence"}
    cleaned = []
    for entry in results:
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise AIEvaluationError(f"Malformed evaluation entry: {entry!r}")
        cleaned.append({key: entry[key] for key in required})
    return cleaned


def ping() -> bool:
    """True if the Ollama server is reachable."""
    try:
        return (
            requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=5).status_code
            == 200
        )
    except requests.RequestException:
        return False
