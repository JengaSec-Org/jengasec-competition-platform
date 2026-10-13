from django.urls import path
from . import views


from . import leaderboard_views, preview_views


app_name = "judging"

urlpatterns = [
    # path("rubrics/", views.rubric_builder, name="rubric_builder"),
    path("ai-results/", views.ai_evaluation_results, name="ai_results"),
    path("ai-results/<int:eval_id>/", views.ai_evaluation_detail, name="ai_result_detail"),
    path("scoring-config/", views.scoring_configuration, name="scoring_config"),



    path("ai-results/<int:eval_id>/run/", views.run_ai_evaluation, name="run_ai_evaluation"),



    path("leaderboard/", leaderboard_views.leaderboard_view, name="leaderboard"),
    path("leaderboard/<int:competition_id>/", leaderboard_views.leaderboard_view, name="leaderboard"),
    path("ai-results/<int:eval_id>/preview/", preview_views.document_preview, name="document_preview"),

]