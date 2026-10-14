"""How a team's final mark is composed.

Three stages, 100 marks, confirmed 13 October 2026:

    Entry            30   the proposal, or the Hack The Box qualifier
    Main competition 40   Morani: what the team did during the three days
    Documentation    30   the written record

Data rather than prose, for the same reason the programme is: judges ask
"what is this out of" at the moment they are marking, and the answer has
to come from one place that the dashboards, the info pages and the
scoring engine all read.

**The stage weights are decided. The rows inside each stage are derived**
from `contracts/findings.md` (valid, reproducible, in scope; severity;
first valid report wins), architecture section 11 (what each team type is
scored on) and the Guide's submission requirements. They are a defensible
starting point, not a published rule — change them here and every surface
follows.

One stage is deliberately not split by side: Entry is marked out of 100
against whichever route the team took, then scaled to 30. A proposal
answers to the published section weights in the Participant FAQ; a
qualifier answers to the Hack The Box score. Flattening those two into
one list of criteria would pretend they are the same exercise.
"""

# (key, label, marks) within a stage. Marks are out of the stage weight.
MAIN_BY_SIDE = {
    "application_red": [
        ("valid", "Valid and reproducible", 16,
         "A judge can repeat it from the report. A finding nobody can "
         "reproduce from the report scores nothing — the track page says so, "
         "so it is a gate, not a deduction. The first valid report of an "
         "issue wins it; a later duplicate scores nothing, which is how "
         "getting there first is rewarded."),
        ("severity", "Severity and impact", 16,
         "What an attacker actually gains. Severity is set by judges, "
         "never by the reporter."),
        ("conduct", "Scope and conduct", 8,
         "Staying inside the assigned target, no denial of service, no "
         "tampering with logs or evidence. Starts full and falls."),
    ],
    "application_blue": [
        ("availability", "Availability under attack", 12,
         "The service still answering while it is being attacked."),
        ("detected", "Attacks detected", 12,
         "Raised from the team's own telemetry. An app that does not log "
         "cannot be credited for detecting anything."),
        ("response", "Response quality", 10,
         "What the team did about it, within the rules, with code frozen."),
        ("unfound", "Flaws Red did not find", 6,
         "Credit for defences that held, not only incidents survived. Every "
         "brief is built twice, and the comparison between the two builds is "
         "part of what is judged."),
    ],
    "ai_blue": [
        ("precision", "Detection precision", 12,
         "Alerts that were really attack traffic, against labelled ground "
         "truth. Blocking a legitimate customer is scored as damage."),
        ("recall", "Detection rate and time to detect", 10,
         "Attack traffic the agent caught, and how quickly — the track page "
         "asks every team for both in its evaluation plan."),
        ("correlation", "Correlation and hybrid design", 8,
         "One incident reported as one incident, and deterministic analytics "
         "combined with model reasoning. Prompt-in, verdict-out scores poorly."),
        ("decisions", "Decision-log quality", 6,
         "Complete enough for a judge to reconstruct the reasoning after "
         "the fact."),
        ("safety", "Safety and restraint", 4,
         "Blast-radius cap, action budgets and a kill switch that stops "
         "autonomous action without stopping detection."),
    ],
    "ai_red": [
        ("missed", "Real attacks missed by the paired agent", 16,
         "Attacks that got through without the paired AI Blue noticing."),
        ("false", "Harmful actions induced", 16,
         "Getting the defender to act against legitimate users. The most "
         "valuable finding in the track."),
        ("breadth", "Technique breadth", 8,
         "Injection, evasion, budget exhaustion, baseline poisoning — "
         "rather than one trick repeated."),
    ],
}

DOCUMENTATION = [
    ("complete", "The required set is complete", 8,
     "Source, container image, documentation, security report, "
     "architecture — and, for a proposal, its cover page, declaration of "
     "originality and AI-use statement."),
    ("evidence", "Evidence quality", 8,
     "Timestamped, reproducible, tied to the platform clock. Screenshots "
     "and requests a judge can follow."),
    ("architecture", "Architecture and security report", 8,
     "The design as built, the threat model, and what the controls "
     "actually defend against."),
    ("clarity", "Clarity and professionalism", 6,
     "Written so somebody who was not in the room can follow it."),
]

# ------------------------------------------------------- findings to marks

# The one stage the platform can compute by itself: Application Red's main
# competition mark comes from findings it already holds, judged by people.
#
# Fixed points with a cap, not a curve. A curve would be defensible, but a
# team watching the board mid-event would see its mark move because somebody
# else scored, which is impossible to explain from the floor. These numbers
# are the published rule: count, severity, who got there first, and conduct.
FINDING_MARKS = {
    # marks per valid finding, up to the criterion's cap
    "valid_each": 2,
    # severity of the issue, per the judge's rating
    "severity_each": {"critical": 5, "high": 3, "medium": 2, "low": 1},
    # conduct starts full and is reduced; it is the only criterion that falls
    "conduct_penalty": 2,
}


def finding_marks(findings):
    """Marks per criterion for one Application Red team's findings.

    `findings` is every finding the team filed in the competition. Returns
    {criterion_key: marks}, each already capped at that criterion's share.
    """
    from cyberrange.models import Finding

    caps = {key: marks for key, _, marks, _ in MAIN_BY_SIDE["application_red"]}
    # A duplicate is not valid, so it contributes nothing here. That is the
    # whole of "the first valid report wins a duplicate" — there is no
    # separate bonus for winning a race, because a team that finds issues
    # nobody else found would then be unable to reach full marks.
    valid = [f for f in findings if f.status == Finding.Status.VALID]

    severity = sum(
        FINDING_MARKS["severity_each"].get(f.severity, 0) for f in valid
    )
    out_of_scope = sum(
        1 for f in findings if f.status == Finding.Status.OUT_OF_SCOPE
    )

    return {
        "valid": min(len(valid) * FINDING_MARKS["valid_each"], caps["valid"]),
        "severity": min(severity, caps["severity"]),
        "conduct": max(
            caps["conduct"] - out_of_scope * FINDING_MARKS["conduct_penalty"], 0
        ),
    }


STAGES = [
    {
        "key": "entry",
        "name": "Entry",
        "weight": 30,
        "summary": "How the team earned its place.",
        "routes": [
            ("Application Blue · AI Defence Blue · AI Red",
             "The proposal, marked out of 100 against the published section "
             "weights, then scaled to 30."),
            ("Application Red",
             "The Hack The Box qualifier score, scaled to 30."),
        ],
        "rows": [],
    },
    {
        "key": "main",
        "name": "Main competition",
        "weight": 40,
        "summary": "Morani Hunting Grounds: the three engagement days.",
        "routes": [],
        "rows": [],  # per side; see rows_for()
    },
    {
        "key": "documentation",
        "name": "Documentation",
        "weight": 30,
        "summary": "The written record, submitted by the Friday deadline.",
        "routes": [],
        "rows": DOCUMENTATION,
    },
]

# Marks earned beyond the hundred. The qualifier is the first case: a team
# that beat the mark that earns full Entry keeps the excess as bonus rather
# than having it clipped off, so finishing far ahead still counts for
# something. Deliberately outside STAGES — it has no cap and is not part of
# the hundred, and a total may therefore read 104/100.
BONUS_STAGE = "bonus"
BONUS_LABEL = "Bonus"


# What the rest of the app reads: (label, weight) pairs summing to 100.
SCORE_WEIGHTS = [(stage["name"], stage["weight"]) for stage in STAGES]

# Guards the thing that would be embarrassing to get wrong.
assert sum(weight for _, weight in SCORE_WEIGHTS) == 100


def side_key(team):
    """Which column of the main-competition table applies to a team."""
    from accounts.models import Team
    from competitions.constants import Track

    if team is None or not team.track:
        return None
    red = team.team_type == Team.TeamType.RED
    if team.track == Track.AI:
        return "ai_red" if red else "ai_blue"
    return "application_red" if red else "application_blue"


def rows_for(stage_key, team=None):
    """The criteria inside one stage, for this team's side where it matters."""
    if stage_key == "documentation":
        return DOCUMENTATION
    if stage_key == "main":
        key = side_key(team)
        return MAIN_BY_SIDE.get(key, []) if key else []
    return []


def stages(team=None):
    """The whole scheme, resolved for one team (or in general)."""
    out = []
    for stage in STAGES:
        rows = rows_for(stage["key"], team) or stage["rows"]
        out.append({
            **stage,
            "rows": [
                {"key": k, "label": label, "marks": marks, "note": note}
                for k, label, marks, note in rows
            ],
        })
    return out


def check():
    """Every side's main-competition rows must sum to the stage weight.

    Called by the tests. A table that does not add up is the kind of thing
    nobody notices until a team asks why it is marked out of 38.
    """
    main = next(s for s in STAGES if s["key"] == "main")
    problems = []
    for key, rows in MAIN_BY_SIDE.items():
        total = sum(marks for _, _, marks, _ in rows)
        if total != main["weight"]:
            problems.append(f"{key} sums to {total}, not {main['weight']}")
    doc = next(s for s in STAGES if s["key"] == "documentation")
    doc_total = sum(marks for _, _, marks, _ in DOCUMENTATION)
    if doc_total != doc["weight"]:
        problems.append(f"documentation sums to {doc_total}, not {doc['weight']}")
    return problems
