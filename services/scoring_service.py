"""Score aggregation service.

Combines AI suggestions, judge overrides, and rubric weights into final
scores. Judge overrides always take precedence and must carry a logged
justification (audit trail lives in the judging/audit modules).
"""


'''
def weighted_score(criterion_scores: list[dict]) -> float:
    """Aggregate per-criterion scores into a weighted total.

    Each entry: {"score": float, "max": float, "weight": float}  (weights sum to 1.0)
    """
    raise NotImplementedError("Team lead: score aggregation engine.")


def final_team_score(component_scores: dict[str, float], component_weights: dict[str, float]) -> float:
    """Combine document-level scores (e.g. proposal 20%, final doc 50%, red team report 30%)."""
    raise NotImplementedError
'''


from decimal import Decimal


def weighted_score(criterion_scores):
    """criterion_scores: list of {"score", "max", "weight"}.

    Weights may be percentages (30, 35, 20, 15) or fractions (0.5, 0.5);
    they are normalised, so both work. Returns a number on a 0-100 scale.
    """
    if not criterion_scores:
        raise ValueError("criterion_scores must not be empty")
    total_weight = 0.0
    acc = 0.0
    for c in criterion_scores:
        mx = float(c["max"])
        w = float(c["weight"])
        if mx <= 0:
            raise ValueError("max must be greater than zero")
        if w < 0:
            raise ValueError("weight must not be negative")
        acc += (float(c["score"]) / mx) * w
        total_weight += w
    if total_weight <= 0:
        raise ValueError("weights must sum to more than zero")
    return round(acc / total_weight * 100, 2)


def final_team_score(component_scores, component_weights):
    """component_scores / component_weights: dicts keyed by component name.

    e.g. scores {"proposal": 80, "documentation": 70, "report": 90},
         weights {"proposal": 20, "documentation": 50, "report": 30} -> 78.0
    """
    if not component_scores:
        raise ValueError("component_scores must not be empty")
    total_w = 0.0
    acc = 0.0
    for key, score in component_scores.items():
        if key not in component_weights:
            raise ValueError(f"no weight for component '{key}'")
        w = float(component_weights[key])
        if w < 0:
            raise ValueError("weight must not be negative")
        acc += float(score) * w
        total_w += w
    if total_w <= 0:
        raise ValueError("weights must sum to more than zero")
    return round(acc / total_w, 2)


# ---------------------------------------------------------------- DB helpers

COMPONENTS = ("proposal", "documentation", "report")


def classify_submission_type(name):
    """Map a SubmissionType name to proposal / documentation / report."""
    n = (name or "").lower()
    if "proposal" in n:
        return "proposal"
    if "report" in n:
        return "report"
    if "documentation" in n or "docs" in n:
        return "documentation"
    return None


def _average(values):
    values = [Decimal(v) for v in values]
    return (sum(values) / len(values)).quantize(Decimal("0.01"))


def team_component_scores(team):
    """Return {component: Decimal average of approved evaluations or None}."""
    from judging.models import Evaluation

    buckets = {c: [] for c in COMPONENTS}
    evals = (
        Evaluation.objects.filter(
            submission__team=team,
            status__in=Evaluation.DONE_STATUSES,
            weighted_total__isnull=False,
        )
        .select_related("submission__submission_type")
    )
    for ev in evals:
        comp = classify_submission_type(ev.submission.submission_type.name)
        if comp:
            buckets[comp].append(ev.weighted_total)
    return {c: (_average(v) if v else None) for c, v in buckets.items()}


def get_weights(team):
    """Weights from ScoringConfig for the team's competition and type."""
    from judging.models import ScoringConfig

    cfg = ScoringConfig.objects.filter(
        competition=team.competition, team_type=team.team_type, is_active=True
    ).first()
    if cfg is None:
        return {"proposal": 20, "documentation": 50, "report": 30}
    return {
        "proposal": cfg.proposal_weight,
        "documentation": cfg.documentation_weight,
        "report": cfg.report_weight,
    }


def team_result(team):
    """Full result row for one team."""
    comps = team_component_scores(team)
    missing = [c for c, v in comps.items() if v is None]
    result = {
        "team": team,
        "components": comps,
        "missing": missing,
        "complete": not missing,
        "final": None,
    }
    if not missing:
        result["final"] = final_team_score(comps, get_weights(team))
    return result


def leaderboard(competition):
    """Complete teams ranked high to low, then incomplete teams."""
    from accounts.models import Team

    rows = [team_result(t) for t in Team.objects.filter(competition=competition)]
    done = sorted((r for r in rows if r["complete"]), key=lambda r: -r["final"])
    todo = sorted((r for r in rows if not r["complete"]), key=lambda r: r["team"].team_name)
    for i, r in enumerate(done, 1):
        r["rank"] = i
    return done + todo