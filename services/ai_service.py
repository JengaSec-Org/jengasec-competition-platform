"""AI evaluation service (Ollama).

Pipeline: parsed document -> prompt builder -> LLM -> per-criterion
evaluation {score, evidence, reasoning, confidence}.

AI output is decision support only — every evaluation must pass through
human judge review (judging module) before scores are final.
"""
from django.conf import settings


def build_prompt(document_text: str, rubric: dict) -> str:
    """Compose the evaluation prompt for one document against one rubric."""
    raise NotImplementedError("Pair 2: prompt engineering lands here.")


def evaluate_document(document_text: str, rubric: dict) -> list[dict]:
    """Run the LLM evaluation and return one result dict per criterion.

    Expected shape per criterion:
        {"criterion": "Innovation", "score": 18, "evidence": "...",
         "reasoning": "...", "confidence": 0.8}
    """
    raise NotImplementedError(
        f"Call Ollama at {settings.OLLAMA_BASE_URL} (model: {settings.OLLAMA_MODEL})."
    )
