"""Email / in-app notifications.

Schema reference: JengaSec Database Design V1, section 4 (notifications).
"""
from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        # Account and team onboarding (Module 1/2 events; several are
        # published for those flows to call -- see docs/HANDOFF-notifications.md).
        ACCOUNT_CREATED = "account_created", "Account Created"
        EMAIL_VERIFIED = "email_verified", "Email Verified"
        INVITATION_SENT = "invitation_sent", "Invitation Sent"
        INVITATION_ACCEPTED = "invitation_accepted", "Invitation Accepted"
        OUTSTANDING_ITEMS = "outstanding_items", "Outstanding Items"
        REGISTRATION_SUBMITTED = "registration_submitted", "Registration Submitted"
        REGISTRATION_APPROVED = "registration_approved", "Registration Approved"
        REGISTRATION_REJECTED = "registration_rejected", "Registration Rejected"
        REGISTRATION_CLOSING = "registration_closing", "Registration Closing"
        MEMBERSHIP_CHANGED = "membership_changed", "Membership Changed"
        POLICY_UPDATED = "policy_updated", "Policy Updated"
        # Submissions, judging and results.
        SUBMISSION_CONFIRMED = "submission_confirmed", "Submission Confirmed"
        EVAL_COMPLETE = "eval_complete", "Evaluation Complete"
        RESULTS_PUBLISHED = "results_published", "Results Published"
        APPEAL_DEADLINE = "appeal_deadline", "Appeal Deadline Approaching"
        APPEAL_SUBMITTED = "appeal_submitted", "Appeal Submitted"
        APPEAL_RESOLVED = "appeal_resolved", "Appeal Resolved"
        DEADLINE_APPROACHING = "deadline_approaching", "Deadline Approaching"
        GENERAL = "general", "General"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    type = models.CharField(max_length=40, choices=Type.choices, default=Type.GENERAL)
    subject = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    # Where clicking the notification takes you. Always an in-site path —
    # notifications.views.mark_read refuses to follow anything else.
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)

    # Email delivery. `send_email` marks a notification as owed an email;
    # `sent_at` records that it went. Both null/False means in-app only,
    # which is how staff notifications are created.
    send_email = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_attempts = models.PositiveSmallIntegerField(default=0)

    # Idempotency handle for the event handlers: a stable string identifying
    # "this notification, about this thing, for this reason". Blank means
    # the notification is not deduplicated (ad-hoc announcements).
    dedupe_key = models.CharField(max_length=120, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Powers the unread-badge query.
            models.Index(fields=["user", "is_read"], name="ix_notification_unread"),
            # Powers the email drain.
            models.Index(
                fields=["send_email", "sent_at"], name="ix_notification_pending"
            ),
        ]
        constraints = [
            # Duplicate suppression enforced by the database, not by handlers
            # remembering to check first. A re-fired signal loses the race
            # rather than sending a second copy.
            models.UniqueConstraint(
                fields=["user", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="unique_notification_dedupe",
            )
        ]

    def __str__(self):
        return f"{self.subject} → {self.user.username}"

    def mark_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])

    @property
    def email_pending(self):
        return self.send_email and self.sent_at is None
