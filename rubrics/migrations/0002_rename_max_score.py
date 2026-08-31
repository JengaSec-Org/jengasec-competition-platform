"""Align field name with the V1 design doc: maximum_score -> max_score."""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("rubrics", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="rubriccriterion",
            old_name="maximum_score",
            new_name="max_score",
        ),
    ]
