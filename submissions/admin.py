from django.contrib import admin

from .models import Submission, SubmissionFile, SubmissionType


@admin.register(SubmissionType)
class SubmissionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 0


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "team",
        "competition",
        "submission_type",
        "current_version",
        "status",
        "submitted_at",
    )
    list_filter = ("status", "submission_type", "competition")
    search_fields = ("team__team_name",)
    inlines = [SubmissionFileInline]
