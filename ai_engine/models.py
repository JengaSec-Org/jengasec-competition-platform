"""Immutable audit trail of AI evaluation passes.

Schema reference: JengaSec Database Design V1, section 4
(ai_evaluation_runs, ai_criterion_results).

criterion_scores answers "what is the score now?" — these two tables
answer "what did the AI say, with which model and prompt, and when?".
A judge editing a working score never destroys what the AI produced.
"""
from django.conf import settings
from django.db import models


class AIEvaluationRun(models.Model):
    """One AI evaluation pass over one submission."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    evaluation = models.ForeignKey(
        "judging.Evaluation", on_delete=models.CASCADE, related_name="ai_runs"
    )
    model_name = models.CharField(max_length=100, help_text="llama3, mistral, qwen…")
    model_version = models.CharField(max_length=60, blank=True)
    prompt_version = models.CharField(
        max_length=60, blank=True, help_text="Track prompt-engineering iterations"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    total_tokens = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    # Full model response, kept verbatim for audit and appeals.
    raw_output = models.JSONField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_runs_triggered",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"], name="ix_ai_run_status")]

    def __str__(self):
        return f"AI run #{self.pk} :: {self.model_name} ({self.get_status_display()})"


class AICriterionResult(models.Model):
    """Immutable per-criterion output of one AI run."""

    ai_run = models.ForeignKey(
        AIEvaluationRun, on_delete=models.CASCADE, related_name="results"
    )
    criterion = models.ForeignKey(
        "rubrics.RubricCriterion", on_delete=models.PROTECT, related_name="ai_results"
    )
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    confidence = models.DecimalField(
        max_digits=4, decimal_places=3, null=True, blank=True
    )
    evidence = models.TextField(blank=True)
    explanation = models.TextField(blank=True)
    recommendation = models.TextField(blank=True)

    class Meta:
        ordering = ["criterion__display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["ai_run", "criterion"], name="unique_ai_result_per_criterion"
            )
        ]

    def __str__(self):
        return f"{self.criterion.criterion}: {self.score}"
