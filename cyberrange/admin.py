from django.contrib import admin, messages

from audit.models import AuditLog
from services import vpn_portal
from services.audit_service import record as audit

from .models import Finding, VpnRegistration, WireGuardProfile


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("id", "round", "red_team", "target_team", "title", "status", "severity", "created_at")
    list_filter = ("competition", "round", "status", "severity")
    search_fields = ("title", "technique", "attack_id", "red_team__team_name", "target_team__cell_id")
    raw_id_fields = ("red_team", "target_team", "duplicate_of", "submitted_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(VpnRegistration)
class VpnRegistrationAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "team_code", "role", "status", "participant_id", "updated_at")
    list_filter = ("status", "role")
    search_fields = ("user__username", "team_code", "team__team_name")
    raw_id_fields = ("user", "team")
    readonly_fields = ("role", "team_code", "public_key", "attempt", "participant_id", "status",
                       "last_error", "created_at", "updated_at")
    actions = ["revoke_vpn"]

    def has_add_permission(self, request):
        return False  # people register themselves on the WireGuard page

    @admin.action(description="Revoke VPN access (removes the peer on wireguard-vm)")
    def revoke_vpn(self, request, queryset):
        done = 0
        for registration in queryset.exclude(status=VpnRegistration.Status.REVOKED):
            try:
                vpn_portal.revoke(registration)
            except vpn_portal.PortalError as exc:
                self.message_user(request, f"{registration.user}: {exc}", messages.ERROR)
                continue
            audit(AuditLog.Action.VPN_REVOKED, request=request, target=registration)
            done += 1
        self.message_user(request, f"Revoked {done} registration(s).")


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
