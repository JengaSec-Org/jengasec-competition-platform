"""The Guide's penalty table (section 10), one row per breach.

Percentages per enforcement level. Written from the roadmap rather than
the Guide's own text -- edit the rows in the admin if the table differs.
"""
from django.db import migrations

ROWS = [
    # code, breach, description, strict, standard, relaxed
    ("LATE", "Late submission accepted under the platform-fault path",
     "Applied when an organiser accepts an upload after the hard close.", 10, 5, 0),
    ("PAGES", "Proposal outside the page band",
     "Body pages above or below the band for the proposal type.", 10, 5, 2),
    ("NAMING", "File does not follow the mandatory naming convention",
     "JS26_<ENT>_<CELL>_<TEAMID>_<TYPE>_v<N>.pdf", 5, 2, 0),
    ("FORMAT", "Required section missing or out of order",
     "Beyond the automatic zero for the mapped criterion.", 10, 5, 2),
    ("AI_UNDECLARED", "AI assistance used but not declared",
     "The AI-use statement omits tools that were evidently used.", 25, 15, 10),
    ("SCOPE", "Out-of-scope activity during the engagement",
     "Attacks outside the assigned targets or window (Code of Conduct s.2).", 50, 30, 20),
    ("EVIDENCE", "Claims without supporting evidence",
     "Findings or controls asserted without the evidence bundle to back them.", 15, 10, 5),
    ("CONDUCT", "Code of conduct breach",
     "Up to disqualification; the percentage is the minimum deduction.", 100, 50, 25),
]


def seed(apps, schema_editor):
    PenaltySchedule = apps.get_model("submissions", "PenaltySchedule")
    for order, (code, breach, description, strict, standard, relaxed) in enumerate(ROWS, 1):
        PenaltySchedule.objects.update_or_create(
            code=code,
            defaults={
                "breach": breach,
                "description": description,
                "percent_strict": strict,
                "percent_standard": standard,
                "percent_relaxed": relaxed,
                "display_order": order,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("submissions", "PenaltySchedule").objects.filter(
        code__in=[r[0] for r in ROWS]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [("submissions", "0010_phase_c_submission_rules")]

    operations = [migrations.RunPython(seed, unseed)]
