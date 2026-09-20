"""Forms for creating submissions and uploading versioned files."""
import os

from django import forms

from .models import Submission, SubmissionFile, SubmissionType

# Accepted document formats (planning doc: PDF, DOCX, ZIP evidence bundles).
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".zip")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

# Zip local-file-header and empty-archive signatures, written without
# escape sequences so nothing can mangle them.
ZIP_LOCAL_HEADER = b"PK" + bytes([3, 4])
ZIP_EMPTY_ARCHIVE = b"PK" + bytes([5, 6])

# Leading bytes each accepted format must actually start with. The
# extension is attacker-controlled and so decides nothing on its own:
# a .pdf whose bytes read "MZ" is a Windows executable in a costume.
MAGIC_SIGNATURES = {
    ".pdf": (b"%PDF-",),
    ".docx": (ZIP_LOCAL_HEADER, ZIP_EMPTY_ARCHIVE),
    ".zip": (ZIP_LOCAL_HEADER, ZIP_EMPTY_ARCHIVE),
}

SNIFFED_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".zip": "application/zip",
}


def safe_filename(name):
    """Strip directory components and control characters from an upload name.

    The stored name is echoed back in a Content-Disposition header and shown
    in the UI, so it must not carry path separators or newlines.
    """
    name = os.path.basename(str(name or "")).replace("\\", "")
    name = "".join(ch for ch in name if ch.isprintable())
    return name.strip() or "upload"


def extension_of(name):
    """The accepted extension this filename claims, or "" if none."""
    lowered = str(name or "").lower()
    for extension in ALLOWED_EXTENSIONS:
        if lowered.endswith(extension):
            return extension
    return ""


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
        extension = extension_of(upload.name)
        if not extension:
            raise forms.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            )
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"File is too large ({upload.size // 1048576} MB). Maximum is 50 MB."
            )

        # The bytes must match the claimed extension.
        head = upload.read(8)
        upload.seek(0)
        if not any(head.startswith(sig) for sig in MAGIC_SIGNATURES[extension]):
            raise forms.ValidationError(
                "This file does not look like a real "
                f"{extension.lstrip('.').upper()} document. "
                "Check you uploaded the right file."
            )
        self._sniffed_extension = extension
        return upload

    def populate_metadata(self, instance, uploaded_by=None):
        """Fill the audit fields the V1 schema requires before saving."""
        upload = self.cleaned_data["file"]
        extension = getattr(self, "_sniffed_extension", "") or extension_of(upload.name)
        instance.filename = safe_filename(upload.name)
        instance.file_size = upload.size
        # The sniffed type, never the browser's claim.
        instance.mime_type = SNIFFED_MIME.get(extension, "application/octet-stream")
        instance.uploaded_by = uploaded_by
        return instance
