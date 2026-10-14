from django.contrib import admin

from .models import ScoreEvent, TeamScore


@admin.register(ScoreEvent)
class ScoreEventAdmin(admin.ModelAdmin):
    list_display = ("team", "stage", "criterion", "marks", "source", "day", "recorded_at")
    list_filter = ("competition", "stage", "source", "day")
    search_fields = ("team__team_name", "team__cell_id", "criterion", "note")
    raw_id_fields = ("competition", "team", "finding", "recorded_by")
    readonly_fields = ("recorded_at", "updated_at")


@admin.register(TeamScore)
class TeamScoreAdmin(admin.ModelAdmin):
    list_display = ("team", "entry", "main", "documentation", "total", "computed_at")
    list_filter = ("competition",)
    search_fields = ("team__team_name", "team__cell_id")
    raw_id_fields = ("competition", "team")
    readonly_fields = ("entry", "main", "documentation", "total", "computed_at")

    def has_add_permission(self, request):
        # Derived from ScoreEvent; typing one here would be overwritten on the
        # next recompute. Marks are entered on the scoring screen.
        return False
