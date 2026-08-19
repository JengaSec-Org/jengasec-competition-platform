from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: an audit trail an admin can edit is not an audit trail."""

    list_display = (
        "created_at",
        "actor_label",
        "category",
        "action",
        "target_label",
        "ip_address",
    )
    list_filter = ("category", "action", "created_at")
    search_fields = ("actor_label", "target_label", "description", "ip_address")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
