from django.contrib import admin

from .models import Competition, CompetitionCategory, CompetitionPhase


class CompetitionCategoryInline(admin.TabularInline):
    model = CompetitionCategory
    extra = 1


class CompetitionPhaseInline(admin.TabularInline):
    model = CompetitionPhase
    extra = 1


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ("name", "theme", "status", "start_date", "end_date", "created_by")
    list_filter = ("status",)
    search_fields = ("name", "theme")
    inlines = [CompetitionCategoryInline, CompetitionPhaseInline]

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
