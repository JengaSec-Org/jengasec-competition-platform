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

    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="submissions"
    )
    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="submissions"
    )
    submission_type = models.ForeignKey(
        SubmissionType, on_delete=models.PROTECT, related_name="submissions"
    )
    current_version = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
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
