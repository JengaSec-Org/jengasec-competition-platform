"""Seed the standard JengaSec document types."""
from django.db import migrations

TYPES = [
    ("Proposal", "Initial system proposal evaluated before the build phase."),
    ("Blue Team Documentation", "Final system documentation from blue teams."),
    ("Red Team Documentation", "Final system documentation from red teams."),
    ("Red Team Report", "Vulnerability findings report from red teams."),
    ("Blue Team Report", "Defense findings report from blue teams."),
]


def seed(apps, schema_editor):
    SubmissionType = apps.get_model("submissions", "SubmissionType")
    for name, description in TYPES:
        SubmissionType.objects.get_or_create(
            name=name, defaults={"description": description}
        )


def unseed(apps, schema_editor):
    SubmissionType = apps.get_model("submissions", "SubmissionType")
    SubmissionType.objects.filter(name__in=[n for n, _ in TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
