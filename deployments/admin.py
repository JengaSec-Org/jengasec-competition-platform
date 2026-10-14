from django.contrib import admin

from .models import AppRuntimeStatus, AppSubmission


@admin.register(AppSubmission)
class AppSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "id", "team", "attempt", "image_reference", "status", "frozen_at", "created_at",
    )
    list_filter = ("competition", "status", "needs_database")
    search_fields = ("image_reference", "registry_username", "team__team_name", "team__cell_id")
    raw_id_fields = ("team", "competition", "created_by")
    readonly_fields = ("attempt", "created_at", "status_changed_at")
    # The token is a team's registry credential: it is set through the team's
    # own form and read by the range, never edited or viewed here.
    exclude = ("pull_token_encrypted",)

    def has_add_permission(self, request):
        return False  # attempts come from the team's submission form


@admin.register(AppRuntimeStatus)
class AppRuntimeStatusAdmin(admin.ModelAdmin):
    list_display = ("team", "status", "healthy", "checked_at")
    list_filter = ("status", "healthy")
    search_fields = ("team__team_name", "team__cell_id", "team__team_identifier")
    readonly_fields = (
        "team", "status", "healthy", "observed_image_digest", "detail", "checked_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
