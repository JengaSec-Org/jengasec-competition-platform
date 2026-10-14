"""What a blue team submits so its application can run on the range.

Teams do not upload code or image files. They push a private image to a
registry they control and tell us where it is, together with the short
form the standard cell chart needs -- port, health path, whether it wants
a database (architecture section 7.1, and the Image Submission Guide the
teams were given).

One row per attempt, never overwritten, for the same reason document
uploads keep versions: the freeze has to be able to name exactly which
image was pinned, and a team that breaks a working submission must leave
the working one on the record.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from services import secret_box


class AppSubmission(models.Model):
    """One attempt at handing an application to the range."""

    class Status(models.TextChoices):
        # The four checks the Guide promises the team, in order, plus the
        # two ways an attempt can stop.
        SUBMITTED = "submitted", "Submitted"
        PULLED = "pulled", "Pulled"
        SCANNED = "scanned", "Scanned"
        DEPLOYED = "deployed", "Deployed"
        HEALTHY = "healthy", "Healthy"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    # The ordered checks, for the progress strip and for deciding whether
    # one status is further along than another.
    CHECKS = [Status.PULLED, Status.SCANNED, Status.DEPLOYED, Status.HEALTHY]
    TERMINAL = (Status.FAILED, Status.REJECTED)

    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="app_submissions"
    )
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="app_submissions"
    )
    attempt = models.PositiveIntegerField(help_text="Counts up per team; never reused.")

    image_reference = models.CharField(
        max_length=300,
        help_text="Full name including registry and tag, e.g. "
                  "ghcr.io/amina-dev/internet-banking-portal:v3",
    )
    registry_username = models.CharField(
        max_length=150, help_text="The account that owns the image."
    )
    # Someone else's credential: encrypted at rest, write-only everywhere.
    pull_token_encrypted = models.TextField(blank=True)

    port = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
        help_text="The port your app listens on inside the container, e.g. 8080.",
    )
    health_path = models.CharField(
        max_length=200, default="/healthz",
        help_text="Must return HTTP 200 when the app is ready.",
    )
    needs_database = models.BooleanField(
        default=False, help_text="We run PostgreSQL 16 in your namespace and pass its details in."
    )
    env_vars = models.JSONField(
        default=dict, blank=True,
        help_text="Non-secret settings only, e.g. APP_MODE=production.",
    )
    extra_images = models.JSONField(
        default=list, blank=True,
        help_text="Up to two more images, for example a worker. Same token.",
    )

    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.SUBMITTED
    )
    status_detail = models.TextField(
        blank=True, help_text="Why it failed, in words the team can act on."
    )
    status_changed_at = models.DateTimeField(null=True, blank=True)
    scan_report = models.JSONField(null=True, blank=True)
    logs_excerpt = models.TextField(blank=True)

    # Set at the freeze, by the organisers or the range -- never by a team.
    frozen_digest = models.CharField(max_length=500, blank=True)
    frozen_extra_images = models.JSONField(default=list, blank=True)
    frozen_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="app_submissions",
    )

    class Meta:
        ordering = ["-attempt"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "competition", "attempt"], name="unique_attempt_per_team"
            )
        ]
        indexes = [
            models.Index(fields=["competition", "status"], name="ix_appsub_comp_status")
        ]

    def __str__(self):
        return f"{self.team.cell_label or self.team.team_name} attempt {self.attempt}"

    # ---------------------------------------------------------- the token

    def set_pull_token(self, plaintext):
        self.pull_token_encrypted = secret_box.encrypt(plaintext)

    @property
    def has_pull_token(self):
        return bool(self.pull_token_encrypted)

    def reveal_pull_token(self):
        """The plaintext token. Only for the range, and audited at the call site."""
        return secret_box.decrypt(self.pull_token_encrypted)

    # ---------------------------------------------------------- progress

    @property
    def is_passing(self):
        return self.status == self.Status.HEALTHY

    @property
    def has_stopped(self):
        return self.status in self.TERMINAL

    @property
    def reached(self):
        """The checks passed so far, for the progress strip."""
        if self.status in self.CHECKS:
            return self.CHECKS[: self.CHECKS.index(self.status) + 1]
        return []

    @property
    def progress(self):
        """Every check with whether it is done, for the template."""
        done = set(self.reached)
        return [
            {"status": check, "label": check.label, "done": check in done}
            for check in self.CHECKS
        ]


class AppRuntimeStatus(models.Model):
    """Latest health and deployed-image observation reported by the range."""

    class Status(models.TextChoices):
        BUILDING = "building", "Building"
        FROZEN = "frozen", "Frozen"
        LIVE = "live", "Live"
        DOWN = "down", "Down"

    team = models.OneToOneField(
        "accounts.Team", on_delete=models.CASCADE, related_name="app_runtime_status"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.BUILDING
    )
    healthy = models.BooleanField(default=False)
    observed_image_digest = models.CharField(max_length=500, blank=True)
    detail = models.TextField(blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.team.cell_label or self.team.team_name}: {self.status}"
