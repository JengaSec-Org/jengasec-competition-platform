"""Score aggregation service.

Combines AI suggestions, judge overrides, and rubric weights into final
scores. Judge overrides always take precedence and must carry a logged
justification (audit trail lives in the judging/audit modules).
"""


def weighted_score(criterion_scores: list[dict]) -> float:
    """Aggregate per-criterion scores into a weighted total.

    Each entry: {"score": float, "max": float, "weight": float}  (weights sum to 1.0)
    """
    raise NotImplementedError("Team lead: score aggregation engine.")


def final_team_score(component_scores: dict[str, float], component_weights: dict[str, float]) -> float:
    """Combine document-level scores (e.g. proposal 20%, final doc 50%, red team report 30%)."""
    raise NotImplementedError
