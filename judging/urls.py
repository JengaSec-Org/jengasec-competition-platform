from django.urls import path
from . import views, appeal_views

app_name = "judging"

urlpatterns = [
    path("rubrics/", views.rubric_builder, name="rubric_builder"),
    path("ai-results/", views.ai_evaluation_results, name="ai_results"),
    path("ai-results/<int:eval_id>/", views.ai_evaluation_detail, name="ai_result_detail"),
    path("scoring-config/", views.scoring_configuration, name="scoring_config"),
    path("appeals/", appeal_views.appeal_list, name="appeal_list"),
    path("appeals/<int:pk>/", appeal_views.appeal_detail, name="appeal_detail"),
    path("appeals/<int:pk>/withdraw/", appeal_views.withdraw_appeal, name="appeal_withdraw"),
]
