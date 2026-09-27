from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.registration_analytics, name="registration"),
    path("<int:pk>/", views.registration_analytics, name="registration_for"),
]
