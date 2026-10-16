"""Marks, and where each one came from.

`ScoreEvent` is the current state of a logical mark. `ScoreRevision` keeps
every accepted version with its reason and calculation inputs. `TeamScore`
is the current sum. Policy enrollment and private evidence bind automatic
marks to their measurement window and reproducible inputs.

The scheme itself -- the three stages, their weights and the criteria
inside them -- lives in `competitions.grading`. Nothing here repeats it,
because a mark capped at 14 in one file and 12 in another is the kind of
disagreement nobody finds until a team appeals.

**Revisions are the record; the total is derived.** `TeamScore` can always be
thrown away and rebuilt from accepted revisions with
`scoring_service.recompute()`, which is what makes the delayed participant
board possible: the same rebuild, with a cut-off time.
"""
from decimal import Decimal
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from cyberrange.models import ROUND_CHOICES


class ScoreEvent(models.Model):
    """One mark awarded to one team, for one reason."""

    class Source(models.TextChoices):
        # Computed by the platform from findings it already holds.
        FINDING = "finding", "From a finding"
        # Pushed by the observability side (uptime, AI precision and recall).
        TELEMETRY = "telemetry", "From telemetry"
        # Typed by a judge on the marking screen.
        JUDGE = "judge", "Judge"
        # Carried in from the proposal or the qualifier.
        ENTRY = "entry", "Entry score"
        # Earned beyond the hundred; see grading.BONUS_STAGE.
        BONUS = "bonus", "Bonus"

    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="score_events"
    )
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="score_events"
    )
    # Keys from competitions.grading: "entry" / "main" / "documentation",
    # and the criterion within that stage.
    stage = models.CharField(max_length=20)
    criterion = models.CharField(max_length=40, blank=True)
    marks = models.DecimalField(max_digits=6, decimal_places=2)
    day = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=ROUND_CHOICES,
        help_text="Which engagement day this came from, where that applies.",
    )

    source = models.CharField(max_length=20, choices=Source.choices)
    finding = models.ForeignKey(
        "cyberrange.Finding", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="score_events",
    )
    note = models.TextField(
        blank=True, help_text="Why. Shown to the team with its mark."
    )

    # Re-running a computation must not double-count, and a judge changing a
    # finding's severity must change the mark rather than add a second one.
    # Everything computed carries a key; everything typed by hand does not.
    dedupe_key = models.CharField(max_length=120, blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="score_events",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    override_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["-recorded_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "team", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="unique_scored_once",
            )
        ]
        indexes = [
            models.Index(fields=["competition", "team", "stage"], name="ix_score_team_stage"),
            models.Index(fields=["competition", "recorded_at"], name="ix_score_recorded"),
        ]

    def __str__(self):
        label = self.criterion or self.stage
        return f"{self.team} · {label} · {self.marks}"


class TeamScore(models.Model):
    """The running total, rebuilt from events.

    Stored rather than computed per request because the venue scoreboard
    is refreshed by a room full of people at once, and because a judge
    needs to see the same number twice.
    """

    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="team_scores"
    )
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="score"
    )

    entry = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    main = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    documentation = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0")
    )
    # Outside the hundred and uncapped, so `total` can exceed 100.
    bonus = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"))

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-total", "team__team_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "team"], name="one_score_per_team"
            )
        ]

    def __str__(self):
        tail = f" (+{self.bonus} bonus)" if self.bonus else ""
        return f"{self.team}: {self.total}/100{tail}"

    def by_stage(self):
        return {
            "entry": self.entry,
            "main": self.main,
            "documentation": self.documentation,
            "bonus": self.bonus,
        }


class ScoringPolicyEnrollment(models.Model):
    """Frozen measurement window, policy and approved evidence capacity."""

    competition = models.OneToOneField(
        "competitions.Competition", on_delete=models.PROTECT, related_name="scoring_policy"
    )
    policy_version = models.CharField(max_length=40)
    policy_sha256 = models.CharField(max_length=64)
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    archive_budget_bytes = models.PositiveBigIntegerField()
    enrolled_at = models.DateTimeField(default=timezone.now)
    reconciled_at = models.DateTimeField()
    finalized_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="scoring_enrollments",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(window_end__gt=models.F("window_start")),
                                   name="scoring_window_positive"),
            models.CheckConstraint(condition=models.Q(archive_budget_bytes__gt=0),
                                   name="scoring_archive_budget_positive"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if any(getattr(previous, name) != getattr(self, name) for name in (
                "competition_id", "policy_version", "policy_sha256", "window_start", "window_end",
                "enrolled_at", "reconciled_at"
            )):
                raise ValueError("an enrolled scoring policy and measurement window are frozen")
            if previous.finalized_at and self.finalized_at != previous.finalized_at:
                raise ValueError("finalized scoring can only be corrected through explicit overrides")
        return super().save(*args, **kwargs)


class ScoringReadiness(models.Model):
    """Current collection and delivery status; never a source of marks."""

    class Status(models.TextChoices):
        COLLECTING = "collecting", "Collecting observations"
        INSUFFICIENT_DATA = "insufficient_data", "Insufficient observations"
        ATTRIBUTION_UNRESOLVED = "attribution_unresolved", "Assignment verification pending"
        PUBLICATION_FAILED = "publication_failed", "Publication pending"
        PUBLISHED = "published", "Verified marks published"

    competition = models.ForeignKey("competitions.Competition", on_delete=models.CASCADE)
    team = models.ForeignKey("accounts.Team", on_delete=models.CASCADE)
    criterion = models.CharField(max_length=40)
    policy_version = models.CharField(max_length=40)
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    status = models.CharField(max_length=30, choices=Status.choices)
    reason_code = models.CharField(max_length=80)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["competition", "team", "criterion"],
                                                name="scoring_readiness_component")]


class ScoringEvidence(models.Model):
    """A verified private archive. No direct public storage URLs are exposed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT)
    family = models.CharField(max_length=20)
    archive = models.FileField(upload_to="scoring/evidence/")
    sha256 = models.CharField(max_length=64)
    input_sha256 = models.CharField(max_length=64)
    snapshot = models.JSONField()
    byte_size = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)
    verified_at = models.DateTimeField()
    retain_until = models.DateTimeField(null=True, blank=True)
    appeal_hold = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["competition", "team", "sha256"],
                                    name="scoring_evidence_once"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            if any(getattr(previous, name) != getattr(self, name) for name in (
                "competition_id", "team_id", "family", "archive", "sha256", "input_sha256",
                "snapshot", "byte_size", "created_at", "verified_at"
            )):
                raise ValueError("verified evidence is immutable; archive a new snapshot")
        return super().save(*args, **kwargs)


class ImmutableRevisionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("score revisions are immutable; append a correction")

    def delete(self):
        raise ValueError("score revisions are retained for audit and appeals")

    def bulk_update(self, *args, **kwargs):
        raise ValueError("score revisions are immutable; append a correction")


class ScoreRevision(models.Model):
    """Append-only state of one logical score component at acceptance time."""

    event = models.ForeignKey(ScoreEvent, on_delete=models.PROTECT, related_name="revisions")
    sequence = models.PositiveIntegerField()
    producer_revision = models.PositiveBigIntegerField(null=True, blank=True)
    accepted_at = models.DateTimeField(default=timezone.now)
    stage = models.CharField(max_length=20)
    criterion = models.CharField(max_length=40, blank=True)
    marks = models.DecimalField(max_digits=6, decimal_places=2)
    day = models.PositiveSmallIntegerField(null=True, blank=True, choices=ROUND_CHOICES)
    source = models.CharField(max_length=20, choices=ScoreEvent.Source.choices)
    note = models.TextField(blank=True)
    finding = models.ForeignKey("cyberrange.Finding", null=True, blank=True,
                                on_delete=models.PROTECT, related_name="score_revisions")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="score_revisions")
    override_active = models.BooleanField(default=False)
    policy_version = models.CharField(max_length=40, blank=True)
    policy_sha256 = models.CharField(max_length=64, blank=True)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    facts = models.JSONField(default=dict)
    quality = models.JSONField(default=dict)
    assignment_revisions = models.JSONField(default=list)
    evidence = models.JSONField(default=list)
    input_sha256 = models.CharField(max_length=64, blank=True)
    payload_sha256 = models.CharField(max_length=64, blank=True)
    baseline = models.BooleanField(default=False)

    objects = ImmutableRevisionQuerySet.as_manager()

    class Meta:
        ordering = ["-accepted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "sequence"], name="scoring_revision_order"),
            models.UniqueConstraint(fields=["event", "producer_revision"],
                                    condition=models.Q(producer_revision__isnull=False),
                                    name="scoring_producer_revision_once"),
        ]
        indexes = [models.Index(fields=["accepted_at", "event"], name="ix_score_revision_time")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("score revisions are immutable; append a correction")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("score revisions are retained for audit and appeals")

    @property
    def team(self):
        return self.event.team

    @property
    def team_id(self):
        return self.event.team_id

    @property
    def recorded_at(self):
        return self.accepted_at

    @property
    def dedupe_key(self):
        return self.event.dedupe_key
