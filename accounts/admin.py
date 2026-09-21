from django.contrib import admin, messages

from .models import (
    EmailVerification,
    PolicyAcceptance,
    PolicyVersion,
    Team,
    TeamInvitation,
    TeamMember,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user", "role", "institution", "email_verified_at",
        "manual_verification_required", "manual_verified_at", "created_at",
    )
    list_filter = ("role", "manual_verification_required")
    search_fields = ("user__username", "user__email", "institution", "student_number")
    readonly_fields = ("identity_uploaded_at",)


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "expires_at", "used_at")
    readonly_fields = ("token",)
    search_fields = ("user__username", "user__email")


@admin.register(PolicyVersion)
class PolicyVersionAdmin(admin.ModelAdmin):
    """Draft a version here, then *Publish*: that makes it current and
    notifies every competitor to re-accept (services.account_service)."""

    list_display = ("version", "title", "is_current", "published_at", "created_at")
    readonly_fields = ("is_current", "published_at")
    actions = ["publish"]

    @admin.action(description="Publish as the current version (notifies competitors)")
    def publish(self, request, queryset):
        from services import account_service

        if queryset.count() != 1:
            self.message_user(request, "Select exactly one version to publish.", messages.ERROR)
            return
        version = account_service.publish_policy(queryset.first())
        self.message_user(request, f"v{version.version} is now current; competitors notified.")


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "version", "accepted_at", "ip_address")
    list_filter = ("version",)
    search_fields = ("user__username", "user__email")


class TeamMemberInline(admin.TabularInline):
    model = TeamMember
    extra = 1


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("team_name", "track", "competition", "captain", "status")
    list_filter = ("track", "status", "competition")
    search_fields = ("team_name", "institution")
    list_display = (
        "cell_id",
        "team_name",
        "team_type",
        "track",
        "enterprise",
        "responsibility",
        "competition",
        "status",
    )
    list_filter = ("team_type", "track", "enterprise", "status", "competition")
    search_fields = ("team_name", "cell_id", "institution", "responsibility", "team_identifier")
    readonly_fields = ("submitted_at",)
    inlines = [TeamMemberInline]


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ("student_name", "email", "team", "role", "user")
    list_filter = ("role", "team__competition")
    search_fields = ("student_name", "email")


@admin.register(TeamInvitation)
class TeamInvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "team", "status", "invited_by", "created_at", "expires_at")
    list_filter = ("status", "team__competition")
    search_fields = ("email", "team__team_name")
    readonly_fields = ("token",)
