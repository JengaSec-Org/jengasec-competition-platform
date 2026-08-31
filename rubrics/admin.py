from django.contrib import admin

from .models import Rubric, RubricCriterion


class RubricCriterionInline(admin.TabularInline):
    model = RubricCriterion
    extra = 1


@admin.register(Rubric)
class RubricAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "competition",
        "submission_type",
        "is_active",
        "created_by",
        "created_at",
    )
    list_filter = ("competition", "submission_type", "is_active")
    search_fields = ("title",)
    inlines = [RubricCriterionInline]


@admin.register(RubricCriterion)
class RubricCriterionAdmin(admin.ModelAdmin):
    list_display = ("criterion", "rubric", "weight", "max_score", "display_order")
    list_filter = ("rubric__competition",)
    search_fields = ("criterion",)
