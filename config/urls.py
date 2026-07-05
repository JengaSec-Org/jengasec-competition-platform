"""Root URL configuration for the JengaSec Evaluation Platform."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Public event website — served by Django directly so the whole
    # platform ships as one app (fewer moving parts, fewer attack surfaces).
    path(
        "",
        TemplateView.as_view(template_name="website_landing_page.html"),
        name="landing",
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
    # Built-in auth until the accounts module ships its own views
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="accounts/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
