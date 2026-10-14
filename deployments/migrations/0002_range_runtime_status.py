from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_userprofile_privacy_accepted_at_and_more"),
        ("deployments", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsubmission",
            name="frozen_digest",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="appsubmission",
            name="frozen_extra_images",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="AppRuntimeStatus",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("building", "Building"),
                            ("frozen", "Frozen"),
                            ("live", "Live"),
                            ("down", "Down"),
                        ],
                        default="building",
                        max_length=10,
                    ),
                ),
                ("healthy", models.BooleanField(default=False)),
                ("observed_image_digest", models.CharField(blank=True, max_length=500)),
                ("detail", models.TextField(blank=True)),
                ("checked_at", models.DateTimeField(blank=True, null=True)),
                (
                    "team",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="app_runtime_status",
                        to="accounts.team",
                    ),
                ),
            ],
        ),
    ]
