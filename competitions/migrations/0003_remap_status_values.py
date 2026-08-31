"""Remap existing competition status values to the V1 vocabulary."""
from django.db import migrations

# Old value -> V1 value
STATUS_MAP = {
    "registration": "open",
    "active": "in_progress",
}


def forward(apps, schema_editor):
    Competition = apps.get_model("competitions", "Competition")
    for old, new in STATUS_MAP.items():
        Competition.objects.filter(status=old).update(status=new)


def reverse(apps, schema_editor):
    Competition = apps.get_model("competitions", "Competition")
    for old, new in STATUS_MAP.items():
        Competition.objects.filter(status=new).update(status=old)


class Migration(migrations.Migration):

    dependencies = [
        ("competitions", "0002_competition_created_by_competition_updated_at_and_more"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
