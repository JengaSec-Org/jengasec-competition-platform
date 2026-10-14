from django.urls import path

from . import views

app_name = "scoring"

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("live/", views.live, name="live"),
    path("scheme/", views.scheme, name="scheme"),
    path("team/<int:pk>/", views.team_breakdown, name="breakdown"),
    path("team/<int:pk>/mark/", views.mark, name="mark"),
]
