"""python manage.py preflight - quick go/no-go check before the competition."""
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Checks Ollama, model, rubrics, scoring config and settings."

    def line(self, ok, msg):
        self.stdout.write(("[ OK ] " if ok else "[FAIL] ") + msg)
        return ok

    def handle(self, *args, **opts):
        from competitions.models import Competition
        from judging.models import ScoringConfig
        from rubrics.models import Rubric

        results = []

        # Ollama reachable + model pulled
        try:
            with urllib.request.urlopen(settings.OLLAMA_BASE_URL + "/api/tags", timeout=5) as r:
                body = r.read().decode()
            results.append(self.line(True, "Ollama reachable"))
            results.append(
                self.line(settings.OLLAMA_MODEL in body, f"Model {settings.OLLAMA_MODEL} pulled")
            )
        except Exception as exc:
            results.append(self.line(False, f"Ollama not reachable: {exc}"))

        # Rubrics
        active = Rubric.objects.filter(is_active=True)
        results.append(self.line(active.count() >= 5, f"{active.count()} active rubrics (need 5)"))
        for r in active:
            results.append(self.line(r.weights_valid(), f"Weights = 100: {r.title}"))

        # Scoring config
        for comp in Competition.objects.all():
            n = ScoringConfig.objects.filter(competition=comp, is_active=True).count()
            results.append(self.line(n >= 2, f"{comp.name}: {n} scoring configs (blue + red)"))

        # Settings
        results.append(self.line(not settings.DEBUG, "DEBUG is False"))
        results.append(self.line(settings.ALLOWED_HOSTS != [], "ALLOWED_HOSTS set"))
        results.append(self.line(getattr(settings, "X_FRAME_OPTIONS", "") == "SAMEORIGIN", "X_FRAME_OPTIONS=SAMEORIGIN"))

        failed = results.count(False)
        self.stdout.write(f"\n{len(results) - failed} passed, {failed} failed")