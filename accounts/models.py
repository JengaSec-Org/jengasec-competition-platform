"""User profiles, roles, and team management.

Schema entities: Users (Django User + UserProfile), Teams, TeamMembers.
Roles reuse Django auth Groups (created in migration 0001) so the
dashboard role-routing keeps working; UserProfile.role is the single
source of truth and syncs the matching group on save.
"""
import datetime

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.validators import RegexValidator
from django.db import models

from competitions.constants import (
    EDITION,
    ENTERPRISE_CODES,
    Enterprise,
    Track,
    cell_prefix,
    track_name,
)


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

    # Guide section 6 step 2: competitors must be 18 to 25 on the day the
    # registration is submitted.
    MIN_AGE = 18
    MAX_AGE = 25

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices, blank=True)
    institution = models.CharField(max_length=160, blank=True)

    # -- Email verification (Guide section 6, step 1). A competitor account
    # is inert until the address is proven: no team, no invitation.
    email_verified_at = models.DateTimeField(null=True, blank=True)
    # The address is not at a recognised institution, so an organiser has
    # to confirm student status by hand (from the ID document).
    manual_verification_required = models.BooleanField(default=False)
    manual_verified_at = models.DateTimeField(null=True, blank=True)
    manual_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # -- Profile completion (Guide section 6, step 2).
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(
        max_length=16,
        blank=True,
        validators=[
            RegexValidator(
                r"^\+[1-9]\d{7,14}$",
                "Enter the number in international format, e.g. +254712345678.",
            )
        ],
        help_text="International (E.164) format. Required for the captain and deputy.",
    )
    repo_handle = models.CharField(
        max_length=80, blank=True, help_text="GitHub / GitLab username for repository access."
    )
    student_number = models.CharField(max_length=40, blank=True)
    programme = models.CharField(max_length=120, blank=True)
    year_of_study = models.PositiveSmallIntegerField(null=True, blank=True)
    # Student ID or national ID. Lives under MEDIA_ROOT, which is never
    # served as static files; only staff can fetch it (accounts.views).
    identity_document = models.FileField(
        upload_to="identity/%Y/", blank=True, max_length=255
    )
    identity_uploaded_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display() or 'no role'})"

    # -- verification -----------------------------------------------------

    @property
    def is_email_verified(self):
        """Staff-issued accounts prove their address through the set-password
        link, so only self-registered competitors go through the token."""
        if self.email_verified_at is not None:
            return True
        if self.user.is_staff or self.user.is_superuser:
            return True
        return self.role in (self.Role.ADMIN, self.Role.JUDGE, self.Role.PARTNER)

    @property
    def is_institution_verified(self):
        """Recognised by domain, or confirmed by an organiser."""
        return not self.manual_verification_required or self.manual_verified_at is not None

    # -- eligibility --------------------------------------------------------

    def age_on(self, on_date):
        if self.date_of_birth is None:
            return None
        dob = self.date_of_birth
        years = on_date.year - dob.year
        if (on_date.month, on_date.day) < (dob.month, dob.day):
            years -= 1
        return years

    def is_age_eligible(self, on_date=None):
        """True when 18-25 on `on_date` (today by default); None if no DOB."""
        if self.date_of_birth is None:
            return None
        on_date = on_date or datetime.date.today()
        return self.MIN_AGE <= self.age_on(on_date) <= self.MAX_AGE

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


class EmailVerification(models.Model):
    """A one-time email-verification link (Guide section 6, step 1).

    A fresh token is issued for every request, and the previous ones are
    discarded so only the newest link works. Valid for 24 hours.
    """

    EXPIRY_HOURS = 24

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_verifications"
    )
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"verification for {self.user.username}"

    @property
    def is_open(self):
        from django.utils import timezone

        return self.used_at is None and self.expires_at > timezone.now()


class PolicyVersion(models.Model):
    """One published version of the code of conduct (Guide section 6, step 4).

    Exactly one version is current. Publishing a new one makes everybody
    re-accept: a registration cannot be submitted until every member has
    accepted the version current at that moment.
    """

    version = models.CharField(max_length=20, unique=True, help_text="e.g. 1.0")
    title = models.CharField(max_length=200, default="JengaSec Code of Conduct")
    summary = models.TextField(blank=True, help_text="What changed; goes into the notice.")
    body = models.TextField()
    is_current = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"],
                condition=models.Q(is_current=True),
                name="one_current_policy_version",
            )
        ]

    def __str__(self):
        return f"{self.title} v{self.version}{' (current)' if self.is_current else ''}"

    @classmethod
    def current(cls):
        return cls.objects.filter(is_current=True).first()


class PolicyAcceptance(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="policy_acceptances"
    )
    version = models.ForeignKey(
        PolicyVersion, on_delete=models.CASCADE, related_name="acceptances"
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    # Recorded for the audit trail: who accepted from where.
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-accepted_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "version"], name="one_acceptance_per_version")
        ]

    def __str__(self):
        return f"{self.user.username} accepted v{self.version.version}"


class Team(models.Model):
    class TeamType(models.TextChoices):
        BLUE = "blue", "Blue Team"
        RED = "red", "Red Team"

    # Shared with the competitions app (see competitions/constants.py).
    Track = Track
    Enterprise = Enterprise

    class Status(models.TextChoices):
        # Created and being assembled; the captain may still change anything.
        REGISTERED = "registered", "In progress"
        # Captain submitted the registration; locked while organisers review.
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    # Roster and registration details cannot change in these states.
    LOCKED_STATUSES = ("submitted", "approved")

    # Guide section 6, step 9: the skills declaration length band.
    SKILLS_MIN = 100
    SKILLS_MAX = 1500

    competition = models.ForeignKey(
        "competitions.Competition", on_delete=models.CASCADE, related_name="teams"
    )
    team_name = models.CharField(max_length=120)
    # Issued once, at registration: JS26-B-014 (edition, side, sequence).
    # Appears on every proposal cover page and in the mandatory file name.
    team_identifier = models.CharField(max_length=20, blank=True)
    # Which side of the competition. Decides the captain's and members'
    # platform role (blue_team / red_team) and therefore their dashboard.
    # Cannot change after submission; nobody may be on both sides.
    team_type = models.CharField(max_length=10, choices=TeamType.choices)
    track = models.CharField(
        max_length=15,
        choices=Track.choices,
        blank=True,
        help_text="Application or AI specialisation.",
    )
    # Application teams (blue and red) work against one JengaBank brief.
    # Red teams get theirs assigned; blue teams choose it at registration.
    application_brief = models.ForeignKey(
        "competitions.ApplicationBrief",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="teams",
    )
    # Display copy of the brief name; kept so a deleted brief leaves a trace.
    application_choice = models.CharField(max_length=200, blank=True)
    enterprise = models.CharField(
        max_length=1, choices=Enterprise.choices, blank=True
    )
    # Cell code within the enterprise, e.g. APP03 / AI01 / APPRED02 / AIRED01.
    # Assigned automatically on approval (services/team_service.assign_cell).
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
    # Why a registration was turned down. Sent verbatim to the captain, so
    # it has to say what to correct -- not just that something was wrong.
    rejection_reason = models.TextField(blank=True)

    # -- Registration submission (Guide section 6, step 9). Ranked tracks
    # open to this side, most preferred first; `track` mirrors the first.
    track_preferences = models.JSONField(default=list, blank=True)
    enterprise_preference = models.CharField(
        max_length=1, choices=Enterprise.choices, blank=True
    )
    # 100-1500 characters on what the team can do.
    skills_declaration = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Optional faculty mentor.
    mentor_name = models.CharField(max_length=150, blank=True)
    mentor_email = models.EmailField(max_length=254, blank=True)
    mentor_phone = models.CharField(max_length=16, blank=True)
    mentor_institution = models.CharField(max_length=200, blank=True)

    # -- Welcome pack (Guide section 6, step 12). Filled by organisers on
    # approval; quoted in the approval notice and on the team page.
    repository_url = models.URLField(max_length=300, blank=True)
    namespace = models.CharField(
        max_length=80, blank=True, help_text="Kubernetes namespace / workspace issued to the team."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["team_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "team_name"], name="unique_team_per_competition"
            ),
            # One builder per cell per enterprise -- the same code exists
            # in both JengaBanks because every brief is built twice.
            models.UniqueConstraint(
                fields=["competition", "enterprise", "cell_id"],
                condition=~models.Q(cell_id=""),
                name="unique_cell_per_enterprise",
            ),
            models.UniqueConstraint(
                fields=["team_identifier"],
                condition=~models.Q(team_identifier=""),
                name="unique_team_identifier",
            ),
        ]

    def __str__(self):
        label = self.team_identifier or self.team_name
        return f"{label} [{self.get_team_type_display()}]"

    @property
    def enterprise_code(self):
        """ENTA / ENTB, or '' before assignment."""
        return ENTERPRISE_CODES.get(self.enterprise, "")

    @property
    def cell_label(self):
        """'ENTA · APP03', or '' before a cell is assigned."""
        return f"{self.enterprise_code} · {self.cell_id}" if self.cell_id else ""

    @property
    def display_label(self):
        """Cell-prefixed name, e.g. 'ENTA APP03 · Nyati Defenders'."""
        if self.cell_id:
            return f"{self.enterprise_code} {self.cell_id} · {self.team_name}"
        return self.team_name

    @property
    def track_label(self):
        """The Guide's track name: Application Blue, AI Defence Blue, ..."""
        return track_name(self.team_type, self.track) if self.track else ""

    @property
    def cell_family(self):
        """APP / AI / APPRED / AIRED -- the cell code prefix for this team."""
        return cell_prefix(self.team_type, self.track) if self.track else ""

    @property
    def submits_proposal(self):
        """Application Red is registration-only (Guide section 3)."""
        return not (self.team_type == self.TeamType.RED and self.track == Track.APPLICATION)

    @property
    def edition(self):
        return EDITION

    @property
    def is_locked(self):
        """Submitted or approved: roster and details are frozen."""
        return self.status in self.LOCKED_STATUSES

    @property
    def can_submit(self):
        """States from which the captain may (re)submit the registration."""
        return self.status in (self.Status.REGISTERED, self.Status.REJECTED)

    @property
    def track_preference_labels(self):
        return [track_name(self.team_type, t) for t in self.track_preferences]


class TeamMember(models.Model):
    class MemberRole(models.TextChoices):
        CAPTAIN = "captain", "Captain"
        # Stands in for the captain; must be reachable by phone like them.
        DEPUTY = "deputy", "Deputy"
        MEMBER = "member", "Member"
        # One optional reserve who may substitute once with organiser approval.
        RESERVE = "reserve", "Reserve"

    # Roles the Guide wants a phone number for.
    CONTACT_ROLES = ("captain", "deputy")

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
    # At least one member must be a security specialist (landing page FAQ).
    is_security_specialist = models.BooleanField(default=False)
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


class TeamInvitation(models.Model):
    """A captain's invitation to join their team (Entry Guide section 6, step 6).

    Sent to an email address, because the invitee usually has no account
    yet. Expires after seven days. Accepting requires a logged-in account
    whose address matches, and is refused for anyone already on a team on
    either side -- one person, one team, one side.
    """

    EXPIRY_DAYS = 7

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(max_length=254)
    token = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_invitations",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "email"],
                condition=models.Q(status="pending"),
                name="one_pending_invitation_per_email_per_team",
            )
        ]

    def __str__(self):
        return f"{self.email} -> {self.team.team_name} ({self.status})"

    @property
    def is_open(self):
        from django.utils import timezone

        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
