from django.apps import AppConfig


class ScoringConfig(AppConfig):
    """Marks: where they came from, and what they add up to."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "scoring"
    verbose_name = "Scoring"
