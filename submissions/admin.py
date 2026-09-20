from django.contrib import admin

from .models import ParsedDocument, Submission, SubmissionFile, SubmissionType


@admin.register(SubmissionType)
class SubmissionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 0
    readonly_fields = ("checksum", "file_size", "mime_type", "uploaded_at")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "team",
        "competition",
        "submission_type",
        "application_brief",
        "current_version",
        "status",
        "selection_status",
        "submitted_at",
    )
    list_filter = (
        "status",
        "selection_status",
        "submission_type",
        "competition",
        "application_brief",
    )
    search_fields = ("team__team_name",)
    inlines = [SubmissionFileInline]


@admin.register(SubmissionFile)
class SubmissionFileAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "submission",
        "version",
        "file_size",
        "uploaded_by",
        "uploaded_at",
    )
    list_filter = ("submission__competition", "submission__submission_type")
    search_fields = ("filename", "checksum")
    readonly_fields = ("checksum", "file_size", "mime_type", "uploaded_at")


@admin.register(ParsedDocument)
class ParsedDocumentAdmin(admin.ModelAdmin):
    """Output of the Document Processing Engine — inspect, don't hand-edit.

    Re-run extraction with `python manage.py parse_submissions --force`
    or the action below.
    """

    list_display = ("submission_file", "status", "title", "parser_version", "parsed_at")
    list_filter = ("status", "parser_version")
    search_fields = ("title", "text")
    readonly_fields = (
        "submission_file",
        "status",
        "title",
        "content",
        "text",
        "parser_version",
        "error_message",
        "parsed_at",
    )
    actions = ["reparse"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Re-run document processing on selected files")
    def reparse(self, request, queryset):
        from services import submission_service

        for parsed in queryset.select_related("submission_file"):
            submission_service.parse_file(parsed.submission_file, force=True)
        self.message_user(request, f"Re-parsed {queryset.count()} document(s).")
