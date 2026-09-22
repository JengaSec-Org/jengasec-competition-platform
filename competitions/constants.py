"""Competition-wide choice sets.

Lives outside models.py so both `accounts` and `competitions` can import
it without a circular dependency.

Reference: JengaSec Entry Guide v1.0 (4 Sep 2026), the JengaBank edition.
Two enterprises, both running the JengaBank architecture; four tracks,
which are the cross-product of two specialisations and two sides. The
Cloud track is not offered in this edition.
"""
from django.db import models

EDITION = "JS26"


class Track(models.TextChoices):
    """Team specialisation. Combined with the team's side (blue / red) this
    gives the four Guide tracks: Application Blue, AI Defence Blue,
    Application Red, AI Red."""

    APPLICATION = "application", "Application"
    AI = "ai", "AI"


class Enterprise(models.TextChoices):
    """Two independent JengaBank instances. Every brief is built twice --
    once in each -- so the comparison between them is part of what is
    judged."""

    A = "a", "ENTA — JengaBank A"
    B = "b", "ENTB — JengaBank B"


# Short forms used in cell codes and the mandatory file name
# (JS26_<ENTERPRISE>_<CELL>_<TEAMID>_<TYPE>_v<N>.pdf).
ENTERPRISE_CODES = {Enterprise.A: "ENTA", Enterprise.B: "ENTB"}


def track_name(team_type, track):
    """The Guide's name for a side + specialisation, e.g. 'AI Defence Blue'."""
    names = {
        ("blue", Track.APPLICATION): "Application Blue",
        ("blue", Track.AI): "AI Defence Blue",
        ("red", Track.APPLICATION): "Application Red",
        ("red", Track.AI): "AI Red",
    }
    return names.get((team_type, track), "")


def cell_prefix(team_type, track):
    """Cell code family for a side + specialisation: APP / AI / APPRED / AIRED."""
    base = "APP" if track == Track.APPLICATION else "AI"
    return base if team_type == "blue" else base + "RED"


# ---------------------------------------------------------------------------
# Proposal documents (Entry Guide sections 7, 9 and 10)
# ---------------------------------------------------------------------------
# NOTE: the bands, section names and penalty table below were written from
# the roadmap rather than the Guide's own text. Adjust the numbers here;
# nothing else needs to change.

# The <TYPE> segment of the mandatory file name, per proposal type name
# (services.submission_service.PROPOSAL_TYPES).
PROPOSAL_FILE_CODES = {
    "Application Blue Proposal": "APPBLUE",
    "AI Defence Proposal": "AIDEF",
    "AI Red Proposal": "AIRED",
}

# Placeholder segments while a team has no enterprise / cell yet (cells are
# assigned at proposal selection, Guide section 4).
UNASSIGNED_SEGMENT = "TBA"

# Body-page band (min, max) per proposal type. Body pages are the total
# minus the cover, contents, references and appendix pages.
PROPOSAL_PAGE_BANDS = {
    "Application Blue Proposal": (8, 15),
    "AI Defence Proposal": (8, 15),
    "AI Red Proposal": (6, 12),
}

PROPOSAL_MAX_BYTES = 25 * 1024 * 1024
EVIDENCE_MAX_BYTES = 50 * 1024 * 1024

# Front matter every proposal must carry; missing any returns the
# submission as incomplete (Guide section 9).
FRONT_MATTER = {
    "cover_page": ("Cover page", ("cover page", "cover sheet")),
    "declaration": ("Declaration of originality", ("declaration",)),
    "ai_use_statement": ("AI-use statement", ("ai-use statement", "ai use statement", "use of ai", "ai usage")),
}

# Required body sections: (key, display name, heading aliases, rubric
# criterion keyword). A section is present when a heading contains one of
# its aliases; a missing section zeroes the criterion whose name contains
# the keyword (Guide section 9).
REQUIRED_SECTIONS = [
    ("executive_summary", "Executive summary", ("executive summary", "summary"), "summary"),
    ("problem", "Problem statement", ("problem statement", "problem", "background"), "problem"),
    ("solution", "Proposed solution", ("proposed solution", "solution overview", "our solution"), "solution"),
    ("architecture", "Architecture", ("architecture", "system design"), "architecture"),
    ("threat_model", "Threat model", ("threat model", "threat analysis", "threats"), "threat"),
    ("security_controls", "Security controls", ("security controls", "controls", "security design"), "security"),
    ("implementation", "Implementation plan", ("implementation plan", "implementation", "build plan"), "implementation"),
    ("testing", "Testing and validation", ("testing", "validation", "test plan"), "testing"),
    ("team", "Team and roles", ("team and roles", "team roles", "the team", "roles"), "team"),
    ("timeline", "Timeline", ("timeline", "schedule", "milestones"), "timeline"),
    ("risks", "Risks and mitigations", ("risks", "risk register", "mitigations"), "risk"),
    ("references", "References", ("references", "bibliography"), "references"),
]

# Extra sections per track (Guide section 9, track-specific requirements).
TRACK_EXTRA_SECTIONS = {
    "Application Blue Proposal": [
        ("api_contract", "API contract compliance", ("api contract", "api specification", "api design"), "api"),
        ("data_model", "Data model", ("data model", "database design", "schema"), "data"),
        ("deployment", "Deployment configuration", ("deployment", "kubernetes", "ci/cd"), "deployment"),
    ],
    "AI Defence Proposal": [
        ("detection", "Detection logic", ("detection logic", "detection", "analysis and triage"), "detection"),
        ("response", "Response actions and guardrails", ("response", "guardrails", "permitted actions"), "response"),
        ("decision_logging", "Decision logging", ("decision logging", "logging", "audit trail"), "logging"),
    ],
    "AI Red Proposal": [
        ("attack_surface", "Attack surface analysis", ("attack surface", "target analysis"), "attack surface"),
        ("attack_plan", "Attack plan", ("attack plan", "methodology", "approach"), "attack"),
        ("evidence_plan", "Evidence plan", ("evidence", "reporting plan"), "evidence"),
    ],
}

# Pages that do not count as body (front matter and back matter):
# detected from the page's first lines.
NON_BODY_PAGE_MARKERS = (
    "cover page", "declaration", "ai-use statement", "ai use statement",
    "contents", "table of contents", "references", "bibliography", "appendix", "appendices",
)


def proposal_file_pattern(team, type_name, version):
    """The exact file name the Guide requires for this upload."""
    code = PROPOSAL_FILE_CODES.get(type_name, "PROPOSAL")
    ent = ENTERPRISE_CODES.get(team.enterprise, "") or UNASSIGNED_SEGMENT
    cell = team.cell_id or UNASSIGNED_SEGMENT
    ident = team.team_identifier or UNASSIGNED_SEGMENT
    return f"{EDITION}_{ent}_{cell}_{ident}_{code}_v{version}.pdf"


def required_sections_for(type_name):
    return REQUIRED_SECTIONS + TRACK_EXTRA_SECTIONS.get(type_name, [])
