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
    # Guide s.7 platform-fault path, s.10 penalties and appeals
    path("<int:pk>/accept-late/", views.accept_late, name="accept_late"),
    path("<int:pk>/penalties/", views.apply_penalty, name="apply_penalty"),
    path("<int:pk>/penalties/<int:penalty_id>/remove/", views.remove_penalty, name="remove_penalty"),
    path("<int:pk>/appeal/", views.file_appeal, name="file_appeal"),
    path("<int:pk>/appeals/<int:appeal_id>/judge/", views.assign_second_judge, name="assign_second_judge"),
    path("files/<int:pk>/download/", views.download_file, name="download"),
    path("figures/<int:pk>/", views.figure_image, name="figure"),
]
