"""Email / in-app notifications.

Schema reference: JengaSec Database Design V1, section 4 (notifications).
"""
from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        SUBMISSION_CONFIRMED = "submission_confirmed", "Submission Confirmed"
        EVAL_COMPLETE = "eval_complete", "Evaluation Complete"
        RESULTS_PUBLISHED = "results_published", "Results Published"
        APPEAL_DEADLINE = "appeal_deadline", "Appeal Deadline Approaching"
        APPEAL_RESOLVED = "appeal_resolved", "Appeal Resolved"
        DEADLINE_APPROACHING = "deadline_approaching", "Deadline Approaching"
        GENERAL = "general", "General"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=40, choices=Type.choices, default=Type.GENERAL)
    subject = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Powers the unread-badge query.
            models.Index(fields=["user", "is_read"], name="ix_notification_unread")
        ]

    def __str__(self):
        return f"{self.subject} → {self.user.username}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])
