"""Competition lifecycle: competitions, categories, and phases.

Schema reference: JengaSec Database Design V1, section 4 (competitions).
"""
from django.conf import settings
from django.db import models


class Competition(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open for Registration"
        IN_PROGRESS = "in_progress", "In Progress"
        JUDGING = "judging", "Judging"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=200, unique=True)
    theme = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="competitions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="ck_competition_dates",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def is_open_for_submissions(self):
        return self.status in (self.Status.OPEN, self.Status.IN_PROGRESS)


class CompetitionCategory(models.Model):
    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "competition categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "name"], name="unique_category_per_competition"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.competition.name})"


class CompetitionPhase(models.Model):
    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="phases"
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "name"], name="unique_phase_per_competition"
            )
        ]

    def __str__(self):
        return f"{self.competition.name} :: {self.name}"
