"""Align accounts models with the V1 design doc.

Team.status moves to registered/approved/rejected/withdrawn, and audit
timestamps are added (existing rows get their creation time stamped now).
"""
import django.utils.timezone
from django.db import migrations, models

# Old value -> V1 value
TEAM_STATUS_MAP = {
    "pending": "registered",
    "active": "approved",
    "inactive": "withdrawn",
    "disqualified": "rejected",
}


def forward_status(apps, schema_editor):
    Team = apps.get_model("accounts", "Team")
    for old, new in TEAM_STATUS_MAP.items():
        Team.objects.filter(status=old).update(status=new)


def reverse_status(apps, schema_editor):
    Team = apps.get_model("accounts", "Team")
    for old, new in TEAM_STATUS_MAP.items():
        Team.objects.filter(status=new).update(status=old)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="teammember",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True, default=django.utils.timezone.now
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="team",
            name="institution",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name="team",
            name="status",
            field=models.CharField(
                choices=[
                    ("registered", "Registered"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("withdrawn", "Withdrawn"),
                ],
                default="registered",
                max_length=15,
            ),
        ),
        migrations.AlterField(
            model_name="teammember",
            name="email",
            field=models.EmailField(max_length=254),
        ),
        migrations.AlterField(
            model_name="teammember",
            name="role",
            field=models.CharField(
                choices=[("captain", "Captain"), ("member", "Member")],
                default="member",
                max_length=50,
            ),
        ),
        migrations.AlterField(
            model_name="teammember",
            name="student_name",
            field=models.CharField(max_length=150),
        ),
        migrations.RunPython(forward_status, reverse_status),
    ]
