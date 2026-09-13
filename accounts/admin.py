from django.contrib import admin

from .models import Team, TeamJoinRequest, TeamMember, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "institution", "created_at")
    list_filter = ("role",)
    search_fields = ("user__username", "user__email", "institution")


class TeamMemberInline(admin.TabularInline):
    model = TeamMember
    extra = 1


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("team_name", "track", "competition", "captain", "status")
    list_filter = ("track", "status", "competition")
    search_fields = ("team_name", "institution")
    inlines = [TeamMemberInline]


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ("student_name", "email", "team", "role", "user")
    list_filter = ("role", "team__competition")
    search_fields = ("student_name", "email")


@admin.register(TeamJoinRequest)
class TeamJoinRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "status", "requested_at", "decided_at")
    list_filter = ("status", "team")