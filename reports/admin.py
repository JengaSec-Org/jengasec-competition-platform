from django.contrib import admin

from .models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("id", "report_type", "evaluation", "generated_by", "generated_at")
    list_filter = ("report_type",)
    search_fields = ("evaluation__submission__team__team_name",)
