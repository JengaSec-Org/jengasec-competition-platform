from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("choose-role/", views.choose_role, name="choose_role"),
    path("team-form/", views.team_form, name="team_form"),
    path("choose-team/", views.choose_team, name="choose_team"),
    path("team-requests/", views.team_requests, name="team_requests"),
    path(
        "team-requests/<int:request_id>/<str:decision>/",
        views.team_request_decide,
        name="team_request_decide",
    ),
    path(
        "team-members/<int:member_id>/remove/",
        views.team_member_remove,
        name="team_member_remove",
    ),
]