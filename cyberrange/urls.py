from django.urls import path

from . import views

app_name = "cyberrange"

urlpatterns = [
    path("findings/", views.finding_list, name="finding_list"),
    path("findings/new/", views.finding_new, name="finding_new"),
    path("findings/<int:pk>/", views.finding_detail, name="finding_detail"),
    path("findings/<int:pk>/evidence/", views.finding_evidence, name="finding_evidence"),
    path("wireguard/", views.wireguard, name="wireguard"),
    path("wireguard/download/", views.wireguard_download, name="wireguard_download"),
    path("wireguard/register/", views.wireguard_register, name="wireguard_register"),
]
