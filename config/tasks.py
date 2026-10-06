"""The scheduled runner: an HTTP way to run the hourly notification jobs.

Render's free plan has no cron service, so nothing was running
`send_registration_reminders`, `send_deadline_reminders`,
`send_outstanding_digest` or the email queue -- which is why no reminder
had ever been sent. An external scheduler (GitHub Actions; see
.github/workflows/scheduled-tasks.yml) posts here instead.

Guarded by a shared secret rather than a session, because the caller is
a machine with no login. Three deliberate choices:

* **404 when unconfigured.** With no `CRON_TOKEN` set the endpoint does
  not exist as far as a caller can tell, so a deployment that forgot the
  variable does not expose an unauthenticated job runner.
* **`compare_digest`.** Token comparison is constant-time, so a wrong
  token leaks nothing about the right one through timing.
* **POST only.** These jobs send email; a crawler following a link, or a
  browser prefetching one, must not be able to fire them.

The jobs themselves come from `notifications.jobs`, the same list the
staff console offers, so what runs hourly and what an organiser can
click cannot drift apart.
"""
import logging
import secrets

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-Cron-Token"


def _configured_token():
    return (getattr(settings, "CRON_TOKEN", "") or "").strip()


@csrf_exempt  # a machine caller has no session and no CSRF cookie
@never_cache
@require_POST
def run(request):
    """Run every scheduled job and report what each one did."""
    expected = _configured_token()
    if not expected:
        # Not configured: behave as though the route does not exist.
        raise Http404("Not found")

    offered = request.headers.get(TOKEN_HEADER, "")
    if not offered or not secrets.compare_digest(offered, expected):
        logger.warning(
            "Scheduled runner rejected a request with a bad or missing token."
        )
        return JsonResponse({"error": "forbidden"}, status=403)

    from notifications import jobs

    results = jobs.run_scheduled()
    failed = [key for key, row in results.items() if not row["ok"]]
    if failed:
        logger.error("Scheduled jobs failed: %s", ", ".join(failed))

    return JsonResponse(
        {
            "status": "error" if failed else "ok",
            "ran": list(results),
            "failed": failed,
            "results": results,
        },
        # 207 rather than 500: some jobs did run, and the scheduler's log
        # should show "partly failed" rather than "the endpoint is broken".
        status=207 if failed else 200,
    )
