"""
Mock data for the Evaluation & AI Module.

Every function here returns data shaped to look like the real data will, 
once Rubric/Criteria/Evaluation/AIResult models exist.

This means: when we swap mock_data.get_rubrics() for Rubric.objects.all()
in views.py, the templates won't need to change AT ALL, because
they're already consuming the right shape.
"""

RUBRICS = [
    {
        "id": 1,
        "name": "Cybersecurity CTF — Final Round",
        "competition": "National Cyber Defense Challenge 2026",
        "criteria": [
            {"id": 101, "name": "Technical Correctness", "description": "Exploit/defense works as claimed and is reproducible.", "max_points": 30, "weight": 0.35},
            {"id": 102, "name": "Documentation Quality", "description": "Write-up is clear enough for a judge to follow without re-running code.", "max_points": 20, "weight": 0.20},
            {"id": 103, "name": "Originality", "description": "Approach isn't a direct copy of a known public writeup.", "max_points": 20, "weight": 0.20},
            {"id": 104, "name": "Defensive Reasoning", "description": "Team explains *why* the defense holds, not just that it does.", "max_points": 30, "weight": 0.25},
        ],
        "criteria_count": 4,
        "used_in_submissions": 18,
        "status": "active",
    },
    {
        "id": 2,
        "name": "Business Pitch — Regional Heat",
        "competition": "InnovateX Business Case 2026",
        "criteria": [
            {"id": 201, "name": "Market Viability", "description": "Realistic size and reachable go-to-market.", "max_points": 25, "weight": 0.30},
            {"id": 202, "name": "Financial Model", "description": "Assumptions are stated and internally consistent.", "max_points": 25, "weight": 0.30},
            {"id": 203, "name": "Presentation", "description": "Clarity and persuasiveness of the pitch itself.", "max_points": 20, "weight": 0.20},
            {"id": 204, "name": "Team Q&A", "description": "Depth of answers under judge questioning.", "max_points": 20, "weight": 0.20},
        ],
        "criteria_count": 4,
        "used_in_submissions": 9,
        "status": "draft",
    },
]


def get_rubrics():
    return RUBRICS


def get_rubric(rubric_id):
    return next((r for r in RUBRICS if r["id"] == int(rubric_id)), None)


AI_EVALUATIONS = [
    {
        "id": 501,
        "submission_id": "SUB-2026-0142",
        "team_name": "Team A1",
        "rubric_name": "Cybersecurity CTF — Final Round",
        "ai_model": "llama3:70b",
        "confidence": "high",
        "status": "pending_review",  # pending_review | approved | modified
        "ai_overall_score": 83.5,
        "criteria_results": [
            {
                "criterion": "Technical Correctness",
                "max_points": 30,
                "ai_score": 27,
                "ai_comment": "Exploit chain reproduced successfully against the provided binary; privilege escalation step verified against the rubric's PoC checklist.",
                "human_score": None,
                "human_comment": "",
            },
            {
                "criterion": "Documentation Quality",
                "max_points": 20,
                "ai_score": 15,
                "ai_comment": "Write-up covers setup and exploitation but omits the mitigation section required by the rubric.",
                "human_score": None,
                "human_comment": "",
            },
            {
                "criterion": "Originality",
                "max_points": 20,
                "ai_score": 18,
                "ai_comment": "No strong overlap detected against the reference corpus of public writeups for this CTF category.",
                "human_score": None,
                "human_comment": "",
            },
            {
                "criterion": "Defensive Reasoning",
                "max_points": 30,
                "ai_score": 23.5,
                "ai_comment": "Explanation of why the patch closes the vulnerability class is present but doesn't address a related bypass.",
                "human_score": None,
                "human_comment": "",
            },
        ],
    },
    {
        "id": 502,
        "submission_id": "SUB-2026-0157",
        "team_name": "Team A2",
        "rubric_name": "Cybersecurity CTF — Final Round",
        "ai_model": "llama3:70b",
        "confidence": "medium",
        "status": "approved",
        "ai_overall_score": 71.0,
        "criteria_results": [
            {
                "criterion": "Technical Correctness",
                "max_points": 30,
                "ai_score": 24,
                "ai_comment": "Exploit works but required a manual retry not documented in the submission.",
                "human_score": 22,
                "human_comment": "Docked further — judge reproduced with 3 failed attempts before success, reliability concern.",
            },
            {
                "criterion": "Documentation Quality",
                "max_points": 20,
                "ai_score": 16,
                "ai_comment": "Clear structure, includes mitigation section.",
                "human_score": 16,
                "human_comment": "Agreed.",
            },
            {
                "criterion": "Originality",
                "max_points": 20,
                "ai_score": 14,
                "ai_comment": "Partial overlap with a known technique, adapted meaningfully.",
                "human_score": 14,
                "human_comment": "Agreed.",
            },
            {
                "criterion": "Defensive Reasoning",
                "max_points": 30,
                "ai_score": 20,
                "ai_comment": "Solid reasoning, minor gaps on edge cases.",
                "human_score": 19,
                "human_comment": "Agreed with AI, rounded down slightly.",
            },
        ],
    },
    {
        "id": 503,
        "submission_id": "SUB-2026-0163",
        "team_name": "Team A3",
        "rubric_name": "Cybersecurity CTF — Final Round",
        "ai_model": "llama3:70b",
        "confidence": "low",
        "status": "pending_review",
        "ai_overall_score": 54.0,
        "criteria_results": [
            {
                "criterion": "Technical Correctness",
                "max_points": 30,
                "ai_score": 12,
                "ai_comment": "Could not confirm exploit reproduces — submission environment details incomplete. Flagged for human verification.",
                "human_score": None,
                "human_comment": "",
            },
            {
                "criterion": "Documentation Quality",
                "max_points": 20,
                "ai_score": 17,
                "ai_comment": "Well written and structured.",
                "human_score": None,
                "human_comment": "",
            },
            {
                "criterion": "Originality",
                "max_points": 20,
                "ai_score": 15,
                "ai_comment": "Appears original relative to reference corpus.",
                "human_score": None,
                "human_comment": "",
            },
            {
                "criterion": "Defensive Reasoning",
                "max_points": 30,
                "ai_score": 10,
                "ai_comment": "Reasoning section present but low-confidence — model detected internal inconsistency between two paragraphs.",
                "human_score": None,
                "human_comment": "",
            },
        ],
    },
]


def get_ai_evaluations():
    return AI_EVALUATIONS


def get_ai_evaluation(eval_id):
    return next((e for e in AI_EVALUATIONS if e["id"] == int(eval_id)), None)


SCORING_CONFIGS = [
    {
        "id": 1,
        "name": "CTF Final Round — Standard",
        "competition": "National Cyber Defense Challenge 2026",
        "aggregation_method": "weighted_average",
        "ai_weight": 40,
        "human_weight": 60,
        "min_human_judges_required": 2,
        "tie_breaker": "senior_judge_decides",
        "ai_confidence_threshold": "medium",  # below this, force human-only scoring
        "is_active": True,
    },
    {
        "id": 2,
        "name": "Business Pitch — Standard",
        "competition": "InnovateX Business Case 2026",
        "aggregation_method": "human_only",
        "ai_weight": 0,
        "human_weight": 100,
        "min_human_judges_required": 3,
        "tie_breaker": "average_of_all_judges",
        "ai_confidence_threshold": "n/a",
        "is_active": True,
    },
]


def get_scoring_configs():
    return SCORING_CONFIGS
