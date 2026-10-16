from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cyberrange", "0004_merge_scoring_and_runtime"),
        ("deployments", "0003_merge_verified_artifacts_range_runtime"),
    ]

    operations = [
        migrations.AddField(
            model_name="targetidentityinterval",
            name="frozen_extra_digests",
            field=models.JSONField(null=True, blank=True, default=None),
        ),
    ]
