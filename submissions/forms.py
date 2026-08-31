"""Forms for creating submissions and uploading versioned files."""
from django import forms

from .models import Submission, SubmissionFile, SubmissionType

# Accepted document formats (planning doc: PDF, DOCX, ZIP evidence bundles).
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".zip")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


class SubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ["team", "competition", "submission_type"]


class SubmissionTypeForm(forms.ModelForm):
    class Meta:
        model = SubmissionType
        fields = ["name", "description"]


class SubmissionFileForm(forms.ModelForm):
    """Upload a new version of a submission document.

    The view is responsible for assigning `version` (submission.current_version + 1)
    and for stamping filename / size / mime type / checksum via `populate_metadata`.
    """

    class Meta:
        model = SubmissionFile
        fields = ["file"]

    def clean_file(self):
        upload = self.cleaned_data["file"]
        name = upload.name.lower()
        if not name.endswith(ALLOWED_EXTENSIONS):
            raise forms.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            )
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"File is too large ({upload.size // 1048576} MB). Maximum is 50 MB."
            )
        return upload

    def populate_metadata(self, instance, uploaded_by=None):
        """Fill the audit fields the V1 schema requires before saving."""
        upload = self.cleaned_data["file"]
        instance.filename = upload.name
        instance.file_size = upload.size
        instance.mime_type = getattr(upload, "content_type", "") or ""
        instance.uploaded_by = uploaded_by
        return instance
