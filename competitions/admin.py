from django.contrib import admin

from .models import (
    ApplicationBrief,
    AttackScenario,
    Competition,
    CompetitionCategory,
    CompetitionObjective,
    CompetitionPhase,
    CompetitionSettings,
    TargetAssignment,
)


class CompetitionCategoryInline(admin.TabularInline):
    model = CompetitionCategory
    extra = 1


class CompetitionSettingsInline(admin.StackedInline):
    model = CompetitionSettings
    can_delete = False


class CompetitionPhaseInline(admin.TabularInline):
    model = CompetitionPhase
    extra = 1


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "theme",
        "phase",
        "status",
        "start_date",
        "end_date",
        "created_by",
    )
    list_filter = ("phase", "status")
    search_fields = ("name", "theme")
    inlines = [
        CompetitionSettingsInline,
        CompetitionCategoryInline,
        CompetitionPhaseInline,
    ]

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ApplicationBrief)
class ApplicationBriefAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "enterprise",
        "track",
        "competition",
        "proposal_count",
        "proposal_cap",
        "is_open",
    )
    list_filter = ("competition", "enterprise", "track", "is_open")
    search_fields = ("code", "name", "summary")

    @admin.display(description="Proposals")
    def proposal_count(self, obj):
        return obj.proposal_count


@admin.register(AttackScenario)
class AttackScenarioAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "track", "red_points", "blue_points", "is_active")
    list_filter = ("track", "is_active")
    search_fields = ("code", "name", "objective")


@admin.register(TargetAssignment)
class TargetAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "red_team",
        "target_team",
        "competition",
        "endpoint",
        "is_active",
    )
    list_filter = ("competition", "is_active")
    search_fields = ("red_team__team_name", "target_team__team_name", "endpoint")
    autocomplete_fields = ("red_team", "target_team")


@admin.register(CompetitionObjective)
class CompetitionObjectiveAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "team", "max_points")
    list_filter = ("team__competition", "team__track")
    search_fields = ("code", "name")
