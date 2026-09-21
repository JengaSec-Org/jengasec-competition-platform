"""The Cloud track is not offered in the JengaBank edition (Entry Guide s.3).

Deactivates the Cloud attack scenarios seeded by 0005 and moves any Cloud
team or brief onto Application, so nothing is left pointing at a choice
that no longer exists. Reversible only in the sense that nothing is
deleted.
"""
from django.db import migrations


def retire_cloud(apps, schema_editor):
    AttackScenario = apps.get_model("competitions", "AttackScenario")
    ApplicationBrief = apps.get_model("competitions", "ApplicationBrief")
    Team = apps.get_model("accounts", "Team")

    AttackScenario.objects.filter(track="cloud").update(is_active=False)
    ApplicationBrief.objects.filter(track="cloud").update(track="application", is_open=False)
    Team.objects.filter(track="cloud").update(track="application")


class Migration(migrations.Migration):

    dependencies = [
        ("competitions", "0009_remove_applicationbrief_unique_brief_per_competition_and_more"),
        ("accounts", "0009_teaminvitation_remove_teamjoinrequest_team_and_more"),
    ]

    operations = [
        migrations.RunPython(retire_cloud, migrations.RunPython.noop),
    ]
