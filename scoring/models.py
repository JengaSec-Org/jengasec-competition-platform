"""Marks, and where each one came from.

Two tables. `ScoreEvent` is every mark anyone or anything awarded, with
its reason attached. `TeamScore` is the sum, kept so a leaderboard is one
query rather than a fan-out over every finding in the competition.

The scheme itself -- the three stages, their weights and the criteria
inside them -- lives in `competitions.grading`. Nothing here repeats it,
because a mark capped at 14 in one file and 12 in another is the kind of
disagreement nobody finds until a team appeals.

**Events are the record; the total is derived.** `TeamScore` can always be
thrown away and rebuilt from `ScoreEvent` with
`scoring_service.recompute()`, which is what makes the delayed participant
board possible: the same rebuild, with a cut-off time.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

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
        return f"{self.team}: {self.total}/100"

    def by_stage(self):
        return {"entry": self.entry, "main": self.main, "documentation": self.documentation}
