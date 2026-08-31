from django.contrib import admin

from .models import Appeal, CriterionScore, Evaluation, ScoreOverride


class CriterionScoreInline(admin.TabularInline):
    model = CriterionScore
    extra = 0


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "submission",
        "judge",
        "rubric",
        "status",
        "weighted_total",
        "completed_at",
    )
    list_filter = ("status", "rubric__competition")
    search_fields = ("submission__team__team_name", "judge__username")
    inlines = [CriterionScoreInline]


@admin.register(CriterionScore)
class CriterionScoreAdmin(admin.ModelAdmin):
    list_display = (
        "evaluation",
        "criterion",
        "ai_score",
        "judge_score",
        "final_score",
        "confidence",
    )
    list_filter = ("evaluation__status",)


@admin.register(ScoreOverride)
class ScoreOverrideAdmin(admin.ModelAdmin):
    """Audit log — read-only by design."""

    list_display = ("score", "judge", "old_value", "new_value", "created_at")
    list_filter = ("judge",)
    search_fields = ("reason",)
    readonly_fields = ("score", "judge", "old_value", "new_value", "reason", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Appeal)
class AppealAdmin(admin.ModelAdmin):
    list_display = ("id", "team", "submission", "status", "created_at", "resolved_by")
    list_filter = ("status",)
    search_fields = ("team__team_name", "reason")
