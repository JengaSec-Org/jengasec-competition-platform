from django.contrib import admin

from .models import ScoreEvent, ScoreRevision, ScoringEvidence, ScoringPolicyEnrollment, TeamScore


class ReadOnlyScoreAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ScoreEvent)
class ScoreEventAdmin(ReadOnlyScoreAdmin):
    list_display = ("team", "stage", "criterion", "marks", "source", "day", "recorded_at")
    list_filter = ("competition", "stage", "source", "day")
    search_fields = ("team__team_name", "team__cell_id", "criterion", "note")
    raw_id_fields = ("competition", "team", "finding", "recorded_by")
    readonly_fields = ("recorded_at", "updated_at")


@admin.register(TeamScore)
class TeamScoreAdmin(ReadOnlyScoreAdmin):
    list_display = ("team", "entry", "main", "documentation", "total", "computed_at")
    list_filter = ("competition",)
    search_fields = ("team__team_name", "team__cell_id")
    raw_id_fields = ("competition", "team")
    readonly_fields = ("entry", "main", "documentation", "total", "computed_at")

    def has_add_permission(self, request):
        # Derived from ScoreEvent; typing one here would be overwritten on the
        # next recompute. Marks are entered on the scoring screen.
        return False


@admin.register(ScoreRevision)
class ScoreRevisionAdmin(ReadOnlyScoreAdmin):
    list_display = ("event", "sequence", "producer_revision", "marks", "source", "accepted_at")
    list_filter = ("source", "policy_version", "baseline")
    raw_id_fields = ("event", "finding", "recorded_by")


@admin.register(ScoringEvidence)
class ScoringEvidenceAdmin(ReadOnlyScoreAdmin):
    list_display = ("id", "competition", "team", "family", "byte_size", "verified_at", "appeal_hold")
    list_filter = ("competition", "family", "appeal_hold")


@admin.register(ScoringPolicyEnrollment)
class ScoringPolicyEnrollmentAdmin(ReadOnlyScoreAdmin):
    list_display = ("competition", "policy_version", "window_start", "window_end", "finalized_at")
