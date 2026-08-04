from django.urls import path

from . import views

app_name = "submissions"

urlpatterns = [
    path("", views.SubmissionListView.as_view(), name="list"),
    path("<int:pk>/", views.SubmissionDetailView.as_view(), name="detail"),
]
