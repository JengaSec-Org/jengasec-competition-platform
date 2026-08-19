"""Submissions: type lookup, submission threads, and versioned files.

Schema reference: JengaSec Database Design V1, section 4
(submission_types, submissions, submission_files).
"""
import hashlib

from django.conf import settings
from django.db import models


class SubmissionType(models.Model):
    """Lookup table so document types are never hardcoded.

    Seeded by migration 0002.
    """

    name = models.CharField(max_length=60, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Submission(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under Review"
        COMPLETED = "completed", "Completed"

    class SelectionStatus(models.TextChoices):
        """Outcome of proposal selection (Proposal submissions only)."""

        PENDING = "pending", "Pending Review"
        SHORTLISTED = "shortlisted", "Shortlisted"
        SELECTED = "selected", "Selected to Build"
        REJECTED = "rejected", "Not Selected"

    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="submissions"
    )
    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="submissions"
    )
    submission_type = models.ForeignKey(
        SubmissionType, on_delete=models.PROTECT, related_name="submissions"
    )
    # Which lined-up application this proposal targets. Only meaningful
    # for Proposal-type submissions.
    application_brief = models.ForeignKey(
        "competitions.ApplicationBrief",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proposals",
    )
    selection_status = models.CharField(
        max_length=15,
        choices=SelectionStatus.choices,
        default=SelectionStatus.PENDING,
        help_text="Proposal outcome; scoring itself lives in the evaluation.",
    )
    current_version = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Late submissions are accepted, recorded, and left to organisers and
    # judges to decide on — the platform never silently refuses work.
    is_late = models.BooleanField(default=False)
    late_by = models.DurationField(null=True, blank=True)
    marking_excluded = models.BooleanField(
        default=False, help_text="Judges have declined to mark this submission."
    )
    marking_note = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["status"], name="ix_submission_status")]
        constraints = [
            # One submission thread per document type per team per competition;
            # new uploads become versions on the same thread.
            models.UniqueConstraint(
                fields=["team", "competition", "submission_type"],
                name="unique_submission_thread",
            )
        ]

    def __str__(self):
        return f"{self.team.team_name} :: {self.submission_type.name} v{self.current_version}"

    @property
    def latest_file(self):
        return self.files.order_by("-version").first()


class SubmissionFile(models.Model):
    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="files"
    )
    version = models.PositiveIntegerField()
    file = models.FileField(upload_to="submissions/%Y/%m/")
    filename = models.CharField(max_length=255)
    file_size = models.BigIntegerField(default=0, help_text="Size in bytes")
    mime_type = models.CharField(max_length=100, blank=True)
    # SHA-256 of the uploaded bytes — integrity + dispute proof.
    checksum = models.CharField(max_length=64, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_files",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "version"], name="unique_version_per_submission"
            )
        ]

    def __str__(self):
        return f"{self.filename} (v{self.version})"

    @property
    def filepath(self):
        """Storage key of the stored object (V1 schema: filepath)."""
        return self.file.name

    def compute_checksum(self):
        """SHA-256 over the stored file, in chunks (files can be large)."""
        digest = hashlib.sha256()
        self.file.open("rb")
        try:
            for chunk in self.file.chunks():
                digest.update(chunk)
        finally:
            self.file.close()
        return digest.hexdigest()

    @property
    def is_parsable(self):
        return self.filename.lower().endswith((".pdf", ".docx"))


class ParsedDocument(models.Model):
    """Structured output of the Document Processing Engine (Module 5).

    One row per uploaded file. Keeps the extracted title/sections/text so
    the AI engine has a stable, auditable input and a 50-page PDF is only
    read once. Never blocks an upload: a failure is stored here as
    `failed` with the error, and can be re-run.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (unsupported format)"

    submission_file = models.OneToOneField(
        SubmissionFile, on_delete=models.CASCADE, related_name="parsed"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    title = models.CharField(max_length=300, blank=True)
    # Full parser payload: sections, tables, metadata, warnings.
    content = models.JSONField(null=True, blank=True)
    # Flattened text, kept separately for search and AI prompting.
    text = models.TextField(blank=True)
    parser_version = models.CharField(max_length=20, blank=True)
    error_message = models.TextField(blank=True)
    parsed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-parsed_at"]
        indexes = [models.Index(fields=["status"], name="ix_parsed_status")]

    def __str__(self):
        return f"Parse of {self.submission_file.filename} ({self.get_status_display()})"

    @property
    def sections(self):
        return (self.content or {}).get("sections", [])

    @property
    def tables(self):
        return (self.content or {}).get("tables", [])

    @property
    def metadata(self):
        return (self.content or {}).get("metadata", {})

    @property
    def warnings(self):
        return (self.content or {}).get("warnings", [])


class DocumentImage(models.Model):
    """A figure pulled out of a parsed document.

    Architecture diagrams are a scored criterion, so a judge reading the
    parsed view — and an AI scoring from the extracted text — must be
    able to see the pictures, not just a count of them.

    Stored under the same private path as the source documents and served
    only through the permission-checked view.
    """

    parsed_document = models.ForeignKey(
        ParsedDocument, on_delete=models.CASCADE, related_name="images"
    )
    page = models.PositiveIntegerField(null=True, blank=True)
    index = models.PositiveIntegerField(default=0)
    image = models.ImageField(upload_to="submissions/figures/%Y/%m/")
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    image_format = models.CharField(max_length=10, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    caption = models.CharField(
        max_length=300, blank=True, help_text="Nearest heading or preceding line."
    )

    class Meta:
        ordering = ["page", "index"]
        constraints = [
            models.UniqueConstraint(
                fields=["parsed_document", "page", "index"],
                name="unique_figure_position",
            )
        ]

    def __str__(self):
        where = f"p{self.page}" if self.page else "doc"
        return f"Figure {where}#{self.index} ({self.image_format})"
