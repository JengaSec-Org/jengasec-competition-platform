"""Seed the Supporting Evidence document type (Module 4)."""
from django.db import migrations

NAME = "Supporting Evidence"
DESCRIPTION = (
    "Evidence bundle (ZIP) backing a proposal, documentation set or findings report."
)


def seed(apps, schema_editor):
    SubmissionType = apps.get_model("submissions", "SubmissionType")
    SubmissionType.objects.get_or_create(
        name=NAME, defaults={"description": DESCRIPTION}
    )


def unseed(apps, schema_editor):
    SubmissionType = apps.get_model("submissions", "SubmissionType")
    SubmissionType.objects.filter(name=NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0004_parseddocument"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
