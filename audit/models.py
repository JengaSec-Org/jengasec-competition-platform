"""Platform audit trail (Module 13).

Layer 1 of the three logging layers in the Final Competition Overview
(section 12): who did what on the platform. Deliberately separate from
competition telemetry and from the scoring engine's ScoreEvent trail —
millions of technical events must never land in here.

Append-only by construction: nothing in the codebase updates a row, and
the admin refuses add and change.
"""
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    class Category(models.TextChoices):
        AUTH = "auth", "Authentication"
        SUBMISSION = "submission", "Submission"
        COMPETITION = "competition", "Competition"
        JUDGING = "judging", "Judging"
        ADMIN = "admin", "Administration"

    class Action(models.TextChoices):
        # auth
        LOGIN_SUCCESS = "login_success", "Signed in"
        LOGIN_FAILED = "login_failed", "Failed sign-in"
        LOGOUT = "logout", "Signed out"
        ROLE_CHANGED = "role_changed", "Role changed"
        # submissions
        FILE_UPLOADED = "file_uploaded", "Document uploaded"
        SUBMISSION_SUBMITTED = "submission_submitted", "Submission finalised"
        FILE_DOWNLOADED = "file_downloaded", "Document downloaded"
        PROPOSAL_SELECTION = "proposal_selection", "Proposal outcome set"
        MARKING_CHANGED = "marking_changed", "Marking eligibility changed"
        # competitions
        COMPETITION_CREATED = "competition_created", "Competition created"
        COMPETITION_UPDATED = "competition_updated", "Competition updated"
        LIFECYCLE_CHANGED = "lifecycle_changed", "Lifecycle changed"
        # judging
        SCORE_OVERRIDDEN = "score_overridden", "Score overridden"

    # Denormalised alongside the FK so the trail survives user deletion.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    actor_label = models.CharField(max_length=150, blank=True)

    category = models.CharField(max_length=20, choices=Category.choices)
    action = models.CharField(max_length=40, choices=Action.choices)

    # Lightweight pointer — no GenericForeignKey, so a deleted target
    # still reads sensibly in the log.
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    target_label = models.CharField(max_length=200, blank=True)

    description = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="ix_audit_created"),
            models.Index(fields=["action"], name="ix_audit_action"),
            models.Index(fields=["category"], name="ix_audit_category"),
            models.Index(fields=["actor"], name="ix_audit_actor"),
        ]

    def __str__(self):
        who = self.actor_label or "system"
        return f"{self.created_at:%Y-%m-%d %H:%M} {who} {self.get_action_display()}"
