from django.urls import path
from . import views

app_name = "api"
urlpatterns = [
    path("evaluations/<int:pk>/run/", views.RunEvaluationView.as_view(), name="run"),
    path("teams/<int:pk>/score/", views.TeamScoreView.as_view(), name="team_score"),
    path("evaluations/<int:pk>/feedback/", views.FeedbackView.as_view(), name="feedback"),
]