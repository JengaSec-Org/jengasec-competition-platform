from django.contrib import admin

from .models import Finding, IPAllocation, RangeApplication, ScopeAssignment, WireGuardProfile


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


@admin.register(RangeApplication)
class RangeApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "application_instance_id", "service_name", "ip_address", "status", "optional", "enabled"
    )
    list_filter = ("competition", "enterprise", "status", "optional", "enabled")
    search_fields = ("application_instance_id", "namespace", "service_name", "ip_address")
    readonly_fields = ("created_at", "updated_at")


@admin.register(IPAllocation)
class IPAllocationAdmin(admin.ModelAdmin):
    list_display = ("allocation_id", "ip_address", "kind", "team_label", "status", "updated_at")
    list_filter = ("competition", "kind", "team_label", "status")
    search_fields = ("allocation_id", "ip_address", "participant__participant_identifier", "team__team_identifier")
    raw_id_fields = ("participant", "team")
    readonly_fields = ("allocation_id", "updated_at")


@admin.register(ScopeAssignment)
class ScopeAssignmentAdmin(admin.ModelAdmin):
    list_display = ("team", "source_cidr", "application", "active", "valid_from", "valid_until")
    list_filter = ("competition", "active")
    search_fields = ("team__team_identifier", "source_cidr", "application__application_instance_id")
    raw_id_fields = ("target_assignment", "team", "application")
    readonly_fields = ("updated_at",)
