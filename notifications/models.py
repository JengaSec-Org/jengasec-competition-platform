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
    def body_blocks(self):
        """The body split into paragraph / list / field blocks.

        Same parser the branded email uses, so the in-app copy and the
        emailed copy of a notification cannot drift apart in structure.
        """
        from services.email_render import parse_body

        return parse_body(self.body)

    @property
    def email_pending(self):
        return self.send_email and self.sent_at is None


class Announcement(models.Model):
    """A notice pinned to the dashboards of one side, or of everybody.

    `send_announcement` already reaches teams by email and in-app, but an
    email is read once and a notification scrolls away. Something a team
    needs to see every time they open the platform -- "the challenges are
    delayed, word by Wednesday" -- has to live on the dashboard.

    It is a row rather than a block of template text because the whole
    point is that it comes down again: organisers clear `is_active` (or
    let `expires_at` pass) from the admin, with no deploy.
    """

    class Audience(models.TextChoices):
        ALL = "all", "Everybody"
        RED = "red", "Red teams"
        BLUE = "blue", "Blue teams"

    # Blank means every competition, so a notice outlives one edition.
    competition = models.ForeignKey(
        "competitions.Competition",
        on_delete=models.CASCADE,
        related_name="announcements",
        null=True,
        blank=True,
        help_text="Leave blank to show in every competition.",
    )
    audience = models.CharField(
        max_length=10, choices=Audience.choices, default=Audience.ALL
    )
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Plain text. Blank lines separate paragraphs.")
    is_active = models.BooleanField(
        default=True, help_text="Untick to take it down immediately."
    )
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Optional: stops showing after this moment, without anyone remembering to.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_active", "audience"])]

    def __str__(self):
        return f"{self.title} ({self.get_audience_display()})"

    @property
    def paragraphs(self):
        """The body split for templates, so no `linebreaks` filter is needed."""
        return [p.strip() for p in self.body.split("\n\n") if p.strip()]

    @classmethod
    def active_for(cls, team):
        """Live announcements this team should see, newest first.

        Matches the team's side plus anything addressed to everybody, and
        a notice scoped to this competition plus anything unscoped.
        """
        from django.utils import timezone

        if team is None:
            return cls.objects.none()
        return cls.objects.filter(
            models.Q(audience=cls.Audience.ALL) | models.Q(audience=team.team_type),
            models.Q(competition__isnull=True) | models.Q(competition=team.competition),
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()),
            is_active=True,
        )
