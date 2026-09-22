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
        # Structure check found mandatory front matter missing (Guide s.9):
        # sent back to the team with the list; a corrected version can be
        # uploaded and finalised again before the deadline.
        RETURNED_INCOMPLETE = "returned_incomplete", "Returned incomplete"
        UNDER_REVIEW = "under_review", "Under Review"
        COMPLETED = "completed", "Completed"

    class SelectionStatus(models.TextChoices):
        """Outcome of proposal selection (Proposal submissions only)."""

        PENDING = "pending", "Pending Review"
        SHORTLISTED = "shortlisted", "Shortlisted"
        SELECTED = "selected", "Selected to Build"
        # The fourth-strongest proposal per brief. The only fallback if a
        # selected team withdraws -- there is no waitlist (Guide section 4).
        RESERVE = "reserve", "Reserve"
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

    # The deadline is a hard close (Guide section 7). `is_late` is only
    # ever set on the organiser-accepted platform-fault path below: an
    # organiser records the captain's email (its timestamp is the evidence)
    # and that unlocks exactly one more upload.
    is_late = models.BooleanField(default=False)
    late_by = models.DurationField(null=True, blank=True)
    late_accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="late_submissions_accepted",
    )
    late_accepted_at = models.DateTimeField(null=True, blank=True)
    # When the captain's email reporting the platform fault was sent.
    late_evidence_at = models.DateTimeField(null=True, blank=True)
    late_accepted_note = models.CharField(max_length=300, blank=True)
    # Set once the allowed late upload has been used.
    late_upload_used_at = models.DateTimeField(null=True, blank=True)
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

    @property
    def late_upload_open(self):
        """An organiser accepted a platform-fault claim and the one extra
        upload it buys has not been used yet."""
        return self.late_accepted_at is not None and self.late_upload_used_at is None

    @property
    def latest_check(self):
        latest = self.latest_file
        return getattr(latest, "structure_check", None) if latest else None

    @property
    def penalty_percent(self):
        """Total percentage deducted at evaluation (Guide section 10)."""
        return sum((p.percent for p in self.penalties.all()), 0)


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


class ProposalCheck(models.Model):
    """Structure check over a parsed proposal (Guide section 9).

    Front matter (cover, declaration, AI-use statement) is mandatory:
    missing any returns the submission as incomplete. Body sections are
    reported one by one with the rubric criterion each maps to, so a
    missing section scores zero for that criterion automatically.
    """

    class Status(models.TextChoices):
        OK = "ok", "Complete"
        INCOMPLETE = "incomplete", "Front matter missing"
        NOT_CHECKED = "not_checked", "Not checked"

    submission_file = models.OneToOneField(
        SubmissionFile, on_delete=models.CASCADE, related_name="structure_check"
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.NOT_CHECKED)
    # [{"key", "name", "present", "heading"}] for the front matter.
    front_matter = models.JSONField(default=list, blank=True)
    # [{"key", "name", "present", "heading", "criterion"}] per required section.
    sections = models.JSONField(default=list, blank=True)
    body_pages = models.PositiveIntegerField(null=True, blank=True)
    checked_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Check of {self.submission_file.filename} ({self.get_status_display()})"

    @property
    def missing_front_matter(self):
        return [item["name"] for item in self.front_matter if not item["present"]]

    @property
    def missing_sections(self):
        return [item for item in self.sections if not item["present"]]

    @property
    def zeroed_criteria(self):
        """Rubric-criterion keywords that score zero for missing sections."""
        return sorted({item["criterion"] for item in self.missing_sections if item.get("criterion")})


class PenaltySchedule(models.Model):
    """The Guide's penalty table (section 10), one row per breach.

    The percentage applied depends on the competition's enforcement level,
    set after registration closes and shown to teams before the window opens.
    """

    code = models.CharField(max_length=30, unique=True)
    breach = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    percent_strict = models.PositiveSmallIntegerField(default=0)
    percent_standard = models.PositiveSmallIntegerField(default=0)
    percent_relaxed = models.PositiveSmallIntegerField(default=0)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "code"]

    def __str__(self):
        return f"{self.code}: {self.breach}"

    def percent_for(self, level):
        return getattr(self, f"percent_{level}", self.percent_standard)


class SubmissionPenalty(models.Model):
    """A breach recorded against one submission; reduces its weighted total."""

    submission = models.ForeignKey(
        Submission, on_delete=models.CASCADE, related_name="penalties"
    )
    breach = models.ForeignKey(PenaltySchedule, on_delete=models.PROTECT, related_name="applications")
    percent = models.PositiveSmallIntegerField()
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.CharField(max_length=300, blank=True)
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-applied_at"]

    def __str__(self):
        return f"-{self.percent}% {self.breach.code} on {self.submission}"


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
