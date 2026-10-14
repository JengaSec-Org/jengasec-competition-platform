from django.apps import AppConfig


class DeploymentsConfig(AppConfig):
    """How a blue team's application reaches the range.

    Teams hand over an image reference and a read-only token rather than
    source code or an image file (architecture section 7.1), so this app
    stores the reference, the short jengasec.yaml form that goes with it,
    and the four checks the range reports back.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "deployments"
    verbose_name = "Application deployments"
