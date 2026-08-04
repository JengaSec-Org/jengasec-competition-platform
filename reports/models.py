"""Generated reports tied to an evaluation.

Schema reference: JengaSec Database Design V1, section 4 (reports).
"""
from django.conf import settings
from django.db import models


class Report(models.Model):
    class ReportType(models.TextChoices):
        TEAM_FEEDBACK = "team_feedback", "Team Feedback"
        JUDGE_REPORT = "judge_report", "Judge Report"
        COMPETITION_SUMMARY = "competition_summary", "Competition Summary"
        ANALYTICS = "analytics", "Analytics"

    evaluation = models.ForeignKey(
        "judging.Evaluation", on_delete=models.CASCADE, related_name="reports"
    )
    report_type = models.CharField(
        max_length=30, choices=ReportType.choices, default=ReportType.TEAM_FEEDBACK
    )
    strengths = models.TextField(blank=True)
    weaknesses = models.TextField(blank=True)
    recommendations = models.TextField(blank=True)
    overall_comments = models.TextField(blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_generated",
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.get_report_type_display()} :: {self.evaluation}"
