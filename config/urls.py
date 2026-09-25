"""Root URL configuration for the JengaSec Evaluation Platform."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from accounts.security import ThrottledLoginView


@never_cache
def healthz(request):
    """Health check for the load balancer and for deployment verification.

    Runs a real query rather than just returning 200. A check that only proves
    "Django started" is close to worthless here: the landing page at / is a
    TemplateView touching no database, so it answers 200 happily while
    PostgreSQL is completely down — and anything trusting that keeps routing
    traffic to a backend that cannot serve a single real page.

    200  the process is up AND the database answers
    503  take this backend out of rotation
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 — any failure here means unhealthy
        return JsonResponse(
            {"status": "unhealthy", "database": str(exc)[:200]},
            status=503,
        )
    return JsonResponse({"status": "ok", "database": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    # Health check. Unauthenticated, because a load balancer cannot log in, and
    # never cached. Exposes no data beyond up/down.
    path("healthz/", healthz, name="healthz"),
    # Public event website — served by Django directly so the whole
    # platform ships as one app (fewer moving parts, fewer attack surfaces).
    path(
        "",
        TemplateView.as_view(template_name="website_landing_page.html"),
        name="landing",
    ),
    # The rest of the public event website. Each is a standalone page from
    # the design repo (its own inline CSS/JS, like the landing page), served
    # here so the whole site ships as one app on one domain.
    path("prepare/", TemplateView.as_view(template_name="prepare.html"), name="prepare"),
    path("partner/", TemplateView.as_view(template_name="partner.html"), name="partner"),
    path(
        "tracks/application-blue/",
        TemplateView.as_view(template_name="track_application_blue.html"),
        name="track_application_blue",
    ),
    path(
        "tracks/application-red/",
        TemplateView.as_view(template_name="track_application_red.html"),
        name="track_application_red",
    ),
    path(
        "tracks/ai-defence-blue/",
        TemplateView.as_view(template_name="track_ai_defence_blue.html"),
        name="track_ai_defence_blue",
    ),
    path(
        "tracks/ai-red/",
        TemplateView.as_view(template_name="track_ai_red.html"),
        name="track_ai_red",
    ),
    path("dashboard/", include("dashboard.urls")),
    path("accounts/", include("accounts.urls")),
    path("competitions/", include("competitions.urls")),
    path("submissions/", include("submissions.urls")),
    path("judging/", include("judging.urls")),
    path("rubrics/", include("rubrics.urls")),
    path("reports/", include("reports.urls")),
    path("analytics/", include("analytics.urls")),
    path("notifications/", include("notifications.urls")),
    path("audit/", include("audit.urls")),
    # Built-in auth until the accounts module ships its own views.
    # Throttled: repeated failures lock the username+IP pair for a window.
    path(
        "login/",
        ThrottledLoginView.as_view(template_name="accounts/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

# NOTE: MEDIA_ROOT is deliberately NOT served as static files, in DEBUG or
# anywhere else. Everything under it is competition-sensitive (team proposals,
# security reports, evidence bundles); serving it would make every document
# readable by URL guess and bypass the ownership check in
# submissions.views.download_file, which streams the same bytes only to the
# owning team, judges and staff.
#
# In production nginx must NOT alias /media/ either — route document access
# through the application.
