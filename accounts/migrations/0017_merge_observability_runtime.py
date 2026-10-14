from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_userprofile_participant_identifier"),
        ("accounts", "0016_canonical_team_namespaces"),
        ("accounts", "0016_participant_consent"),
    ]

    operations = []
