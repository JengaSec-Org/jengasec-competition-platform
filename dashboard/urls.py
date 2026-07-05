from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    # Post-login role dispatch — LOGIN_REDIRECT_URL points here.
    path("", views.main_dashboard, name="main_dashboard"),
    path("command/", views.admin_dashboard, name="admin_dashboard"),
    path("blue/", views.blue_dashboard, name="blue_dashboard"),
    path("red/", views.red_dashboard, name="red_dashboard"),
    path("insights/", views.insights_dashboard, name="insights_dashboard"),
]

