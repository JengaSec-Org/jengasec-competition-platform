"""User profiles, roles, and team management.

Schema entities: Users (Django User + UserProfile), Teams, TeamMembers.
Roles reuse Django auth Groups (created in migration 0001) so the
dashboard role-routing keeps working; UserProfile.role is the single
source of truth and syncs the matching group on save.
"""
from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models

from competitions.constants import Enterprise, Track


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

        if target != current:
            from audit.models import AuditLog
            from services.audit_service import record

            record(
                AuditLog.Action.ROLE_CHANGED,
                actor=self.user,
                target=self.user,
                description=f"Role set to {self.get_role_display() or 'none'}",
                groups=sorted(target),
            )


class Team(models.Model):
    class TeamType(models.TextChoices):
        BLUE = "blue", "Blue Team"
        RED = "red", "Red Team"

    # Shared with the competitions app (see competitions/constants.py).
    Track = Track
    Enterprise = Enterprise

    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="teams"
    )
    team_name = models.CharField(max_length=120)
    team_type = models.CharField(max_length=10, choices=TeamType.choices)
    track = models.CharField(
        max_length=15,
        choices=Track.choices,
        blank=True,
        help_text="Cloud, Application or AI specialisation.",
    )
    enterprise = models.CharField(
        max_length=1, choices=Enterprise.choices, blank=True
    )
    # Competition cell, e.g. APP-01 / CLOUD-02 / AI-03.
    cell_id = models.CharField(max_length=20, blank=True)
    # The component this team owns, e.g. "Customer Authentication Service".
    responsibility = models.CharField(max_length=200, blank=True)
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
            ),
            models.UniqueConstraint(
                fields=["competition", "cell_id"],
                condition=~models.Q(cell_id=""),
                name="unique_cell_per_competition",
            ),
        ]

    def __str__(self):
        label = self.cell_id or self.team_name
        return f"{label} [{self.get_team_type_display()}]"

    @property
    def display_label(self):
        """Cell-prefixed name, e.g. 'APP-01 · Nyati Defenders'."""
        return f"{self.cell_id} · {self.team_name}" if self.cell_id else self.team_name

    @property
    def track_label(self):
        """Track name, phrased for the side the team is on (Cloud Red, etc.)."""
        if not self.track:
            return ""
        base = self.get_track_display()
        return f"{base} Red" if self.team_type == self.TeamType.RED else f"{base} Blue"


class TeamMember(models.Model):
    class MemberRole(models.TextChoices):
        CAPTAIN = "captain", "Captain"
        MEMBER = "member", "Member"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    # Optional link to a platform account — members may register later.
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
