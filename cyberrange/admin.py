from django.contrib import admin

from .models import Finding, WireGuardProfile


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("id", "round", "red_team", "target_team", "title", "status", "severity", "created_at")
    list_filter = ("competition", "round", "status", "severity")
    search_fields = ("title", "technique", "attack_id", "red_team__team_name", "target_team__cell_id")
    raw_id_fields = ("red_team", "target_team", "duplicate_of", "submitted_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(WireGuardProfile)
class WireGuardProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "address", "issued_at", "downloaded_at", "revoked")
    list_filter = ("revoked",)
    search_fields = ("user__username", "address")
    raw_id_fields = ("user", "team")
    # The file holds a private key: it is replaced by the load command, never edited or viewed here.
    exclude = ("config",)
    readonly_fields = ("issued_at", "downloaded_at")

    def has_add_permission(self, request):
        return False  # profiles come from `manage.py load_wireguard_profiles`
