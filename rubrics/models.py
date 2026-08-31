"""Rubrics: weighted criteria that AI and judges score against.

Schema reference: JengaSec Database Design V1, section 4
(rubrics, rubric_criteria).
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Rubric(models.Model):
    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="rubrics"
    )
    submission_type = models.ForeignKey(
        "submissions.SubmissionType", on_delete=models.PROTECT, related_name="rubrics"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Toggle a rubric off without deleting it (evaluations PROTECT it anyway).
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rubrics_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["competition", "title"]

    def __str__(self):
        return f"{self.title} ({self.submission_type.name})"

    def total_weight(self):
        """Sum of criterion weights — the app requires this to equal 100."""
        return sum((c.weight for c in self.criteria.all()), Decimal("0"))

    def weights_valid(self):
        return self.total_weight() == Decimal("100.00")


class RubricCriterion(models.Model):
    rubric = models.ForeignKey(
        Rubric, on_delete=models.CASCADE, related_name="criteria"
    )
    criterion = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Percentage weight, e.g. 20.00 — criteria on one rubric must sum to 100.
    weight = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    max_score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("100.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name_plural = "rubric criteria"
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["rubric", "criterion"], name="unique_criterion_per_rubric"
            ),
            models.UniqueConstraint(
                fields=["rubric", "display_order"], name="unique_order_per_rubric"
            ),
        ]

    def __str__(self):
        return f"{self.criterion} ({self.weight}%)"
