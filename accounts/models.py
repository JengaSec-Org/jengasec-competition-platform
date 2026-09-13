"""User profiles, roles, and team management.

Schema entities: Users (Django User + UserProfile), Teams, TeamMembers.
Roles reuse Django auth Groups (created in migration 0001) so the
dashboard role-routing keeps working; UserProfile.role is the single
source of truth and syncs the matching group on save.
"""
from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models


class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        JUDGE = "judge", "Judge"
        BLUE_TEAM = "blue_team", "Blue Team"
        RED_TEAM = "red_team", "Red Team"
        PARTNER = "partner", "Partner"

    # Roles that map to auth Groups (admin maps to is_staff instead).
    ROLE_GROUPS = {
        Role.JUDGE: "judge",
        Role.BLUE_TEAM: "blue_team",
        Role.RED_TEAM: "red_team",
        Role.PARTNER: "partner",
    }

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices, blank=True)
    institution = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display() or 'no role'})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.sync_role_group()

    def sync_role_group(self):
        """Keep auth Groups (used by dashboard routing) in step with role."""
        managed = set(self.ROLE_GROUPS.values())
        current = set(
            self.user.groups.filter(name__in=managed).values_list("name", flat=True)
        )
        target = {self.ROLE_GROUPS[self.role]} if self.role in self.ROLE_GROUPS else set()
        for name in current - target:
            self.user.groups.remove(Group.objects.get(name=name))
        for name in target - current:
            group, _ = Group.objects.get_or_create(name=name)
            self.user.groups.add(group)
        if self.role == self.Role.ADMIN and not self.user.is_staff:
            self.user.is_staff = True
            self.user.save(update_fields=["is_staff"])


class Team(models.Model):
    class Track(models.TextChoices):
        CLOUD = "cloud", "Cloud"
        APPLICATION = "application", "Application"
        AI = "ai", "AI"

    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="teams"
    )
    team_name = models.CharField(max_length=120)
    track = models.CharField(max_length=20, choices=Track.choices)
    application_choice = models.CharField(max_length=200, blank=True)
    captain = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="captained_teams",
    )
    institution = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.REGISTERED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["team_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "team_name"], name="unique_team_per_competition"
            )
        ]

    def __str__(self):
        return f"{self.team_name} [{self.get_track_display()}]"


class TeamMember(models.Model):
    class MemberRole(models.TextChoices):
        CAPTAIN = "captain", "Captain"
        MEMBER = "member", "Member"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="team_memberships",
    )
    student_name = models.CharField(max_length=150)
    email = models.EmailField(max_length=254)
    role = models.CharField(
        max_length=50, choices=MemberRole.choices, default=MemberRole.MEMBER
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["team", "student_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "email"], name="unique_member_email_per_team"
            )
        ]

    def __str__(self):
        return f"{self.student_name} ({self.team.team_name})"


class TeamJoinRequest(models.Model):
    """A prospective member's request to join a team, pending captain review.

    Separate from TeamMember: TeamMember rows are the real, confirmed
    roster (and drive the captain/team_member Groups via signals.py), so a
    request only becomes a TeamMember row once approved.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="team_join_requests",
    )
    team = models.ForeignKey(
        Team, on_delete=models.CASCADE, related_name="join_requests"
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="pending"),
                name="one_pending_join_request_per_user",
            )
        ]

    def __str__(self):
        return f"{self.user.username} -> {self.team.team_name} ({self.status})"