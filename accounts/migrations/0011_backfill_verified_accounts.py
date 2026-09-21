"""Accounts that existed before email verification shipped are treated as
verified: they were created by organisers or have already been working on
the platform, and locking them out retroactively would help nobody.
Anyone registering from now on goes through the token."""
from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    UserProfile = apps.get_model("accounts", "UserProfile")
    UserProfile.objects.filter(email_verified_at__isnull=True).update(
        email_verified_at=timezone.now()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_phase_b_registration_flow"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
