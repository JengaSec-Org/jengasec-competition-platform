from django.urls import path

from . import views

app_name = "competitions"

urlpatterns = [
    path("", views.CompetitionListView.as_view(), name="list"),
    path("new/", views.CompetitionCreateView.as_view(), name="create"),
    path("<int:pk>/", views.CompetitionDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.CompetitionUpdateView.as_view(), name="update"),
]
