"""Align field name with the V1 design doc: evaluation_status -> status."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("judging", "0002_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="evaluation",
            old_name="evaluation_status",
            new_name="status",
        ),
    ]
