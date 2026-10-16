from django.apps import AppConfig


class CyberrangeConfig(AppConfig):
    """The site's side of the cyber range: findings, WireGuard profiles, range exports.

    Named `cyberrange`, not `range`, so it never shadows the Python builtin.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "cyberrange"
    verbose_name = "Cyber range"

    def ready(self):
        from . import scoring_signals  # noqa: F401
