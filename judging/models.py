from django.db import models

<<<<<<< Updated upstream
=======
Schema reference: JengaSec Database Design V1, section 4
(evaluations, criterion_scores, score_overrides, appeals).

Works with or without AI: ai_score stays NULL when AI is disabled,
and judge_score always takes precedence when both exist. Every judge
change to a score is logged in ScoreOverride with a mandatory reason.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


class Evaluation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        AI_COMPLETE = "ai_complete", "AI Complete"
        JUDGE_REVIEWING = "judge_reviewing", "Judge Reviewing"
        APPROVED = "approved", "Approved"
        PUBLISHED = "published", "Published"

    IN_PROGRESS_STATUSES = (Status.PENDING, Status.AI_COMPLETE, Status.JUDGE_REVIEWING)
    DONE_STATUSES = (Status.APPROVED, Status.PUBLISHED)

    submission = models.ForeignKey(
        "submissions.Submission", on_delete=models.CASCADE, related_name="evaluations"
    )
    # Nullable: an AI-only first pass has no judge assigned yet.
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="evaluations",
    )
    rubric = models.ForeignKey(
        "rubrics.Rubric", on_delete=models.PROTECT, related_name="evaluations"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # Cached aggregate of final scores — recomputed via recalculate_total().
    weighted_total = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"], name="ix_evaluation_status")]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "judge", "rubric"],
                name="unique_evaluation_assignment",
            )
        ]

    def __str__(self):
        judge = self.judge.username if self.judge else "unassigned"
        return f"Evaluation #{self.pk} :: {self.submission} :: {judge}"

    def recalculate_total(self, save=True):
        """Weighted total = sum(final_score / max_score * weight) across criteria."""
        total = Decimal("0")
        for score in self.criterion_scores.select_related("criterion"):
            if score.final_score is None:
                continue
            criterion = score.criterion
            if not criterion.max_score:
                continue
            total += (score.final_score / criterion.max_score) * criterion.weight
        self.weighted_total = total.quantize(Decimal("0.01"))
        if save:
            self.save(update_fields=["weighted_total", "updated_at"])
        return self.weighted_total


class CriterionScore(models.Model):
    """The current working score a judge sees and edits.

    The immutable record of what the AI originally said lives in
    ai_engine.AICriterionResult — editing here never destroys it.
    """

    evaluation = models.ForeignKey(
        Evaluation, on_delete=models.CASCADE, related_name="criterion_scores"
    )
    criterion = models.ForeignKey(
        "rubrics.RubricCriterion", on_delete=models.PROTECT, related_name="scores"
    )
    ai_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    judge_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    final_score = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    # AI self-reported confidence, 0.000–1.000
    confidence = models.DecimalField(
        max_digits=4, decimal_places=3, null=True, blank=True
    )
    reasoning = models.TextField(blank=True, help_text="Latest AI reasoning")
    evidence = models.TextField(blank=True, help_text="Latest AI evidence")
    comments = models.TextField(blank=True, help_text="Judge comment")

    class Meta:
        ordering = ["criterion__display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["evaluation", "criterion"], name="unique_score_per_criterion"
            )
        ]

    def __str__(self):
        value = self.final_score if self.final_score is not None else "—"
        return f"{self.criterion.criterion}: {value}"

    def save(self, *args, **kwargs):
        # Judge override wins; fall back to AI when no judge score exists.
        if self.final_score is None:
            self.final_score = (
                self.judge_score if self.judge_score is not None else self.ai_score
            )
        super().save(*args, **kwargs)

    def apply_judge_score(self, new_value, judge, reason):
        """Set a judge score and log the change. Reason is mandatory."""
        if not reason:
            raise ValueError("A justification is required to override a score.")
        old_value = self.final_score
        self.judge_score = new_value
        self.final_score = new_value
        self.save()
        ScoreOverride.objects.create(
            score=self,
            judge=judge,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )
        return self


class ScoreOverride(models.Model):
    """Accountability log — every judge change to a score."""

    score = models.ForeignKey(
        CriterionScore, on_delete=models.CASCADE, related_name="overrides"
    )
    judge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="score_overrides",
    )
    old_value = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    new_value = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.TextField(help_text="Mandatory justification")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.score.criterion.criterion}: {self.old_value} → {self.new_value}"


class Appeal(models.Model):
    """A team's dispute of a result."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        UNDER_REVIEW = "under_review", "Under Review"
        UPHELD = "upheld", "Upheld"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    submission = models.ForeignKey(
        "submissions.Submission", on_delete=models.CASCADE, related_name="appeals"
    )
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="appeals"
    )
    reason = models.TextField(help_text="Grounds for appeal")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    resolution = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appeals_filed",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="appeals_resolved",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Appeal #{self.pk} :: {self.team.team_name} ({self.get_status_display()})"
>>>>>>> Stashed changes
