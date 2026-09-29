from django.urls import path

from . import views

app_name = "rubrics"

urlpatterns = [
    path("", views.rubric_list, name="rubric_list"),
    path("new/", views.rubric_create, name="rubric_create"),
    path("<int:pk>/edit/", views.rubric_edit, name="rubric_edit"),
]