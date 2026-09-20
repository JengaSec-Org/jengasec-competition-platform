from django.urls import path

from . import views

app_name = "submissions"

urlpatterns = [
    path("", views.submission_list, name="list"),
    # Upload wizard
    path("upload/", views.wizard_step1, name="wizard_step1"),
    path("upload/document/", views.wizard_step2, name="wizard_step2"),
    path("upload/review/", views.wizard_step3, name="wizard_step3"),
    # Organiser screens
    path("proposals/", views.proposal_review, name="proposal_review"),
    # Existing threads
    path("<int:pk>/", views.submission_detail, name="detail"),
    path("<int:pk>/new-version/", views.upload_new_version, name="new_version"),
    path("<int:pk>/marking/", views.toggle_marking, name="toggle_marking"),
    path("files/<int:pk>/download/", views.download_file, name="download"),
    path("figures/<int:pk>/", views.figure_image, name="figure"),
]
