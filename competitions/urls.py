from django.urls import path

from . import views

app_name = "competitions"

urlpatterns = [
    path("", views.competition_list, name="list"),
    path("new/", views.competition_create, name="create"),
    path("<int:pk>/", views.competition_detail, name="detail"),
    path("<int:pk>/edit/", views.competition_update, name="update"),
    path("<int:pk>/settings/", views.competition_settings, name="settings"),
    path("<int:pk>/lifecycle/", views.competition_lifecycle, name="lifecycle"),
]
