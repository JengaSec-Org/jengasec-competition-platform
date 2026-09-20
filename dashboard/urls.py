from django.urls import path

from . import views

app_name = "dashboard"

# Registration | Build | Live — the phases a team can land on.
PHASE_PATTERN = "<str:phase>"

urlpatterns = [
    # Post-login role dispatch — LOGIN_REDIRECT_URL points here.
    path("", views.main_dashboard, name="main_dashboard"),
    path("command/", views.admin_dashboard, name="admin_dashboard"),
    # Team dashboards follow the competition phase; the explicit phase
    # routes let a team look back at a phase already reached.
    path("blue/", views.blue_dashboard, name="blue_dashboard"),
    # Event-time pages, closed until the competition phase is live.
    # Declared before the phase catch-all so they aren't swallowed by it.
    path("blue/live-status/", views.blue_live_status, name="blue_live_status"),
    path("red/targets/", views.red_target_access, name="red_target_access"),
    path(f"blue/{PHASE_PATTERN}/", views.blue_dashboard, name="blue_phase"),
    path("red/", views.red_dashboard, name="red_dashboard"),
    path(f"red/{PHASE_PATTERN}/", views.red_dashboard, name="red_phase"),
    # Reference material (attack scenarios, scoring, objectives…)
    path("info/", views.info, name="info"),
    path("insights/", views.insights_dashboard, name="insights_dashboard"),
]
