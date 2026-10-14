from django.urls import path

from . import views

app_name = "scoring"

urlpatterns = [
    path("", views.leaderboard, name="leaderboard"),
    path("live/", views.live, name="live"),
    path("scheme/", views.scheme, name="scheme"),
    path("team/<int:pk>/", views.team_breakdown, name="breakdown"),
    path("team/<int:pk>/mark/", views.mark, name="mark"),
    path("revisions/<int:pk>/", views.revision_detail, name="revision"),
    path("revisions/<int:pk>/appeal/", views.file_score_appeal, name="file_appeal"),
    path("revisions/<int:pk>/findings/<int:finding_id>/evidence/", views.download_finding_evidence,
         name="finding_evidence"),
    path("evidence/<uuid:pk>/", views.download_evidence, name="evidence"),
    path("overrides/<int:pk>/release/", views.release_override, name="release_override"),
]
