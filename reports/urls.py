from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.report_index, name="index"),
    path("<int:pk>/<str:kind>.csv", views.export_csv, name="export_csv"),
]
