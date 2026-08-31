from django.contrib import admin

from .models import AICriterionResult, AIEvaluationRun


class AICriterionResultInline(admin.TabularInline):
    model = AICriterionResult
    extra = 0
    readonly_fields = (
        "criterion",
        "score",
        "confidence",
        "evidence",
        "explanation",
        "recommendation",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AIEvaluationRun)
class AIEvaluationRunAdmin(admin.ModelAdmin):
    """Immutable audit trail — inspect only, never edit."""

    list_display = (
        "id",
        "evaluation",
        "model_name",
        "prompt_version",
        "status",
        "total_tokens",
        "latency_ms",
        "created_at",
    )
    list_filter = ("status", "model_name", "prompt_version")
    readonly_fields = (
        "evaluation",
        "model_name",
        "model_version",
        "prompt_version",
        "status",
        "total_tokens",
        "latency_ms",
        "error_message",
        "raw_output",
        "triggered_by",
        "created_at",
        "completed_at",
    )
    inlines = [AICriterionResultInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AICriterionResult)
class AICriterionResultAdmin(admin.ModelAdmin):
    list_display = ("ai_run", "criterion", "score", "confidence")
    list_filter = ("ai_run__model_name",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
