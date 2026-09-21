"""Three registration-phase proposal types (Entry Guide section 3).

Replaces the single "Proposal" document type with one per track that
submits one. Existing proposals are re-pointed by their team's side and
specialisation; Application Red never had a proposal type and gets none.
"""
from django.db import migrations

TYPES = {
    ("blue", "application"): (
        "Application Blue Proposal",
        "Registration-phase proposal for one JengaBank application brief. "
        "12 to 20 body pages, PDF.",
    ),
    ("blue", "ai"): (
        "AI Defence Proposal",
        "Registration-phase proposal for an autonomous defence agent. "
        "12 to 20 body pages, PDF.",
    ),
    ("red", "ai"): (
        "AI Red Proposal",
        "Registration-phase proposal for an autonomous attacking agent, "
        "including its containment design and kill switch. 8 to 14 body pages, PDF.",
    ),
}


def forwards(apps, schema_editor):
    SubmissionType = apps.get_model("submissions", "SubmissionType")
    Submission = apps.get_model("submissions", "Submission")
    Rubric = apps.get_model("rubrics", "Rubric")

    by_key = {}
    for key, (name, description) in TYPES.items():
        by_key[key], _ = SubmissionType.objects.get_or_create(
            name=name, defaults={"description": description}
        )

    old = SubmissionType.objects.filter(name="Proposal").first()
    if old is None:
        return
    fallback = by_key[("blue", "application")]
    for sub in Submission.objects.filter(submission_type=old).select_related("team"):
        new_type = by_key.get((sub.team.team_type, sub.team.track), fallback)
        sub.submission_type = new_type
        sub.save(update_fields=["submission_type"])
    # Rubrics written against the old type follow the Application Blue one.
    Rubric.objects.filter(submission_type=old).update(submission_type=fallback)
    old.delete()


def backwards(apps, schema_editor):
    SubmissionType = apps.get_model("submissions", "SubmissionType")
    Submission = apps.get_model("submissions", "Submission")
    old, _ = SubmissionType.objects.get_or_create(
        name="Proposal",
        defaults={"description": "Initial system proposal evaluated before the build phase."},
    )
    names = [n for n, _ in TYPES.values()]
    Submission.objects.filter(submission_type__name__in=names).update(submission_type=old)
    SubmissionType.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0008_alter_submission_selection_status"),
        ("accounts", "0009_teaminvitation_remove_teamjoinrequest_team_and_more"),
        ("rubrics", "0003_rubric_is_active_rubric_updated_at_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
