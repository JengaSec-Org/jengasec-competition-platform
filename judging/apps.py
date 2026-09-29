from django.apps import AppConfig


class JudgingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "judging"




    def ready(self):
        import judging.signals
