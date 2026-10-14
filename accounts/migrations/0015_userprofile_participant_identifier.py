from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_userprofile_privacy_accepted_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="participant_identifier",
            field=models.CharField(
                blank=True,
                help_text="Opaque competition identifier exposed to operational systems.",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="userprofile",
            constraint=models.UniqueConstraint(
                condition=~models.Q(participant_identifier=""),
                fields=("participant_identifier",),
                name="unique_participant_identifier",
            ),
        ),
    ]
