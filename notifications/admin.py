from django.contrib import admin

from .models import Announcement, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "type", "is_read", "sent_at", "created_at")
    list_filter = ("type", "is_read")
    search_fields = ("subject", "body", "user__username")
    actions = ["mark_as_read"]

    @admin.action(description="Mark selected notifications as read")
    def mark_as_read(self, request, queryset):
        updated = queryset.update(is_read=True)
        self.message_user(request, f"{updated} notification(s) marked as read.")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    """Where a pinned notice is taken down again."""

    list_display = ("title", "audience", "competition", "is_active", "expires_at", "created_at")
    list_filter = ("audience", "is_active")
    list_editable = ("is_active",)
    search_fields = ("title", "body")
