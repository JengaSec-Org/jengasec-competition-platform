from django.urls import path

from . import ingest, views

app_name = "deployments"

urlpatterns = [
    path("", views.submit, name="submit"),
    path("queue/", views.queue, name="queue"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/review/", views.review, name="review"),
    path("<int:pk>/token/", views.reveal_token, name="reveal_token"),
    # Machine endpoints for the range; see deployments/ingest.py.
    path("api/pending/", ingest.pending, name="api_pending"),
    path("api/status/", ingest.status, name="api_status"),
]
