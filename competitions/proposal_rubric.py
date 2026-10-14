"""The proposal rubric, as published in the Entry Guide v1.0.

Section numbers, titles and weights are the Guide's, not ours. Teams were
told to use these exact headings in this exact order, because the platform
finds sections by heading — so changing a title here breaks the structure
check as well as the mark.

**The weights sum to 150, not 100.** That is how the Guide writes them:
they are relative weights, not percentages of a whole. A proposal is
marked by scoring each criterion out of 100, weighting it, and dividing by
the total weight — which gives a mark out of 100 that then becomes the
Entry stage's 30. `normalise()` does exactly that, so nobody has to
remember the 150.

Two sections carry no weight. Executive Summary is framing and Objectives
are not weighted in the Guide; both are still required, and a missing
section zeroes the criterion it belongs to (Guide, penalties table).
"""

# (number, title, weight, what it must contain)
BLUE_SECTIONS = [
    (1, "Executive Summary", 0,
     "One page. What the team is building, for which brief, why it matters, "
     "and what will exist at the end of the build phase."),
    (2, "Problem Statement", 10,
     "The specific security problem this component exists to solve, in its "
     "enterprise context. Not 'cyber attacks are increasing'."),
    (3, "Objectives", 0,
     "Three to six objectives, each specific and verifiable. 'Implement MFA "
     "with recovery' is an objective; 'improve security' is not."),
    (4, "Proposed System", 15,
     "What the service does, its main flows, and its boundaries — including "
     "what it deliberately does not do."),
    (5, "System Significance", 15,
     "Why this component failing would matter to JengaBank, and what an "
     "attacker gains by compromising it."),
    (6, "Functional Requirements", 15,
     "A numbered, testable list covering every mandatory capability in the "
     "brief, plus the team's own additions marked as such. Must include a "
     "compliance table mapping every mandatory capability and every mandatory "
     "endpoint to the requirement that covers it."),
    (7, "Non-functional Requirements", 15,
     "Performance, availability, scalability, maintainability and operability "
     "targets, with numbers attached."),
    (8, "Threat Model", 20,
     "Assets, trust boundaries, actors, attack surface, threats enumerated "
     "using a named method (STRIDE, attack trees or equivalent), and the "
     "risks accepted."),
    (9, "Security Objectives", 20,
     "The controls that answer the threat model, each traced to the threat it "
     "mitigates and to a recognised framework reference."),
    (10, "Proposed Architecture Diagram", 15,
     "At least one component diagram and one data-flow diagram with trust "
     "boundaries marked, embedded as legible images, each with a text "
     "description beneath it."),
    (11, "Technology Stack", 15,
     "Languages, frameworks, libraries, data stores and any AI models, with a "
     "justification for each significant choice and a note on its security "
     "posture."),
    (12, "Development Timeline", 10,
     "Week-by-week plan across the build window, with named owners, "
     "dependencies and milestones. Must include time for testing and "
     "documentation."),
]

# AI Red's proposal is "scored on preparation and professionalism rather than
# on results" (Entry Guide), and the Guide gives its fifteen sections without
# weights. Equal weight is the honest reading of that: no section is named as
# mattering more, and inventing a ranking would be us deciding, not the Guide.
RED_SECTIONS = [
    (1, "Executive Summary",
     "The target cell, the team's approach in one page, and what a successful "
     "engagement would demonstrate."),
    (2, "Target Understanding",
     "What the assigned cell is for, its role in the enterprise, its published "
     "interface, and the team's assessment of its most valuable assets."),
    (3, "Scope Statement",
     "Everything in scope, restated in the team's own words, and everything "
     "out of scope. Errors here are the most common cause of rule breaches."),
    (4, "Rules of Engagement Acknowledgement",
     "Explicit, itemised acceptance of the rules of engagement, signed by "
     "every member."),
    (5, "Threat Actor Profile",
     "The adversary being simulated and its capability and motivation."),
    (6, "Attack Hypotheses",
     "Six to ten specific, testable hypotheses about where the cell is likely "
     "to be weak, derived from its domain and published interface."),
    (7, "Methodology",
     "The phases of the engagement, the standard being followed (PTES, OWASP "
     "WSTG, OSSTMM or equivalent), and the mapping to MITRE ATT&CK tactics."),
    (8, "Reconnaissance Plan",
     "What will be enumerated in the recon window, with what tooling, at what "
     "rate."),
    (9, "Planned Attack Chains",
     "At least three chains from initial access to objective, each with the "
     "expected evidence and the expected impact."),
    (10, "Tooling",
     "Every tool to be used, with versions. Any custom tooling described, with "
     "its intended behaviour and its safety limits."),
    (11, "Safety and Non-Destructive Measures",
     "How the team will avoid data destruction, uncontrolled disruption and "
     "scope escape, including the rate limits it imposes on itself."),
    (12, "Evidence and Chain of Custody",
     "How evidence will be captured, timestamped, stored, redacted and "
     "destroyed."),
    (13, "Team, Roles and Timeline",
     "Who does what in which window, and how the activity log is maintained."),
    (14, "Reporting Plan",
     "The structure of the final report, the severity model with the CVSS "
     "version stated, and how remediation advice will be developed."),
    (15, "Ethics Declaration",
     "Signed statement on lawful conduct, confidentiality of anything "
     "discovered, and the obligation to report real-world risk immediately."),
]

# Guide, penalties table. Applied to the proposal score, not to eligibility.
PENALTIES = [
    ("over_pages", "Over the page limit", 5,
     "Content beyond the limit is not read."),
    ("file_name", "File naming convention not followed", 2, ""),
    ("uncited_passage", "Uncited reuse of a limited passage", 10,
     "Up to this much, plus a formal warning."),
    ("false_ai_statement", "A false AI use statement", 20,
     "Up to this much. A repeat is grounds for disqualification."),
    ("confidentiality", "Confidentiality breach", 25,
     "Up to this much of the stage score. A repeat is disqualification."),
]

# Which rubric a submission type is marked against.
RUBRIC_FOR_TYPE = {
    "Application Blue Proposal": "blue",
    "AI Defence Proposal": "blue",
    "AI Red Proposal": "red",
}

RED_SECTION_WEIGHT = 10  # equal weight, see above


def sections(kind):
    """(number, title, weight, description) for one rubric."""
    if kind == "red":
        return [
            (number, title, RED_SECTION_WEIGHT, description)
            for number, title, description in RED_SECTIONS
        ]
    return BLUE_SECTIONS


def total_weight(kind):
    return sum(weight for _, _, weight, _ in sections(kind))


def normalise(scored, kind="blue"):
    """Weighted criterion scores (each out of 100) into a mark out of 100.

    `scored` maps a section title to its score. A section that was not
    scored counts as zero, because the Guide says a missing section is a
    zero on its criterion and "is not averaged away or inferred from
    elsewhere in the document".
    """
    weights = total_weight(kind)
    if not weights:
        return 0.0
    earned = sum(
        float(scored.get(title, 0)) * weight
        for _, title, weight, _ in sections(kind)
        if weight
    )
    return round(earned / weights, 2)


def to_entry_marks(mark_out_of_100, stage_weight=30):
    """A proposal mark becomes the Entry stage's share."""
    return round(float(mark_out_of_100) * stage_weight / 100, 2)
