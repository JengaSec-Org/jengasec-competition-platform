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
from django.core.exceptions import ValidationError
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
    participant_identifier = models.CharField(max_length=20, blank=True, editable=False)
    role = models.CharField(max_length=20, choices=Role.choices, blank=True)
    institution = models.CharField(max_length=160, blank=True)

    # Somebody who signed up but has no team. `role` is normally set when a
    # person joins one (team_service.assign_platform_role), which left the
    # unassigned with no way to say which side they want and no way to be
    # found -- the old "wait for your captain to invite you" was a dead end
    # for anybody no captain happened to know.
    # Stamped when the data-privacy notice was agreed to at registration.
    # Separate from the code of conduct (PolicyAcceptance): one is consent
    # to our processing their data, the other is agreement to the rules,
    # and an auditor will ask about them separately.
    privacy_accepted_at = models.DateTimeField(null=True, blank=True)
    privacy_accepted_ip = models.GenericIPAddressField(null=True, blank=True)

    seeking_team = models.BooleanField(
        default=False,
        help_text="Wants to be placed on a team that has a seat free.",
    )
    seeking_since = models.DateTimeField(
        null=True, blank=True,
        help_text="Stamped when they joined the pool, so the longest wait is listed first.",
    )

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
        constraints = [models.UniqueConstraint(
            fields=["participant_identifier"], condition=~models.Q(participant_identifier=""),
            name="unique_participant_identifier",
        )]

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
        if not self.participant_identifier:
            self.participant_identifier = f"{EDITION}-P-{self.pk:04d}"
            type(self).objects.filter(pk=self.pk, participant_identifier="").update(
                participant_identifier=self.participant_identifier,
            )
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
    """One published version of a document participants must accept.

    Two kinds, each with its own current version: the code of conduct
    (Guide section 6, step 4) and the Participant Terms and Conditions.
    They are separate documents agreed separately -- the terms carry
    intellectual property, photography and recruiter clauses that have
    nothing to do with the rules of conduct -- so bundling them into one
    tick would make it impossible to say what somebody actually agreed to.

    Exactly one version of each kind is current. Publishing a new one makes
    everybody re-accept that kind: a registration cannot be submitted until
    every member has accepted the versions current at that moment.
    """

    class Kind(models.TextChoices):
        CONDUCT = "conduct", "Code of Conduct"
        TERMS = "terms", "Participant Terms and Conditions"

    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.CONDUCT
    )
    # Unique per kind, not globally: conduct v1.1 and terms v1.1 can coexist.
    version = models.CharField(max_length=20, help_text="e.g. 1.0")
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
                fields=["kind", "is_current"],
                condition=models.Q(is_current=True),
                name="one_current_policy_per_kind",
            ),
            models.UniqueConstraint(
                fields=["kind", "version"], name="one_version_number_per_kind"
            ),
        ]

    def __str__(self):
        return f"{self.title} v{self.version}{' (current)' if self.is_current else ''}"

    @classmethod
    def current(cls, kind=None):
        """The current version of one kind. Defaults to the conduct, which
        is what every existing caller means."""
        return cls.objects.filter(
            is_current=True, kind=kind or cls.Kind.CONDUCT
        ).first()


class ParticipantConsent(models.Model):
    """The optional choices that sit beside the Participant Terms.

    The terms themselves are one required tick, recorded as a
    `PolicyAcceptance` like any other policy. Everything here is optional,
    starts empty and stands alone -- which is the document's own wording,
    and the reason each choice is its own column rather than a list. An
    auditor asked "did this person agree to be photographed?" must get an
    answer about photography, not about a bundle.

    `black_lanyard` is an opt-OUT and the only field whose true value means
    less permission, not more. It is held separately from the media boxes
    because the check-in desk reads it to decide which lanyard to issue,
    and it must be answerable without interpreting nine other fields.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="consent"
    )

    # O1 — clause 5.2. Not photographed or filmed; black lanyard issued.
    black_lanyard = models.BooleanField(default=False)

    # M1–M10 — what the Club may use, and who else may use it.
    m1_name_and_placing = models.BooleanField(default=False)
    m2_quotes = models.BooleanField(default=False)
    m3_project_description = models.BooleanField(default=False)
    m4_portraits = models.BooleanField(default=False)
    m5_campaign_graphics = models.BooleanField(default=False)
    m6_strathmore = models.BooleanField(default=False)
    m7_scesa = models.BooleanField(default=False)
    m8_ncba = models.BooleanField(default=False)
    m9_future_events = models.BooleanField(default=False)
    m10_learning_library = models.BooleanField(default=False)

    # R1–R4 — what recruiters and sponsors may receive.
    r1_contact_details = models.BooleanField(default=False)
    r2_academic_details = models.BooleanField(default=False)
    r3_performance_summary = models.BooleanField(default=False)
    r4_shortlist = models.BooleanField(default=False)

    # Which published version these choices were made against, so a later
    # version cannot silently reinterpret them.
    version = models.ForeignKey(
        "accounts.PolicyVersion", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="consents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Clause 6: withdrawal is by email, and the Club records when it acted.
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawal_note = models.TextField(blank=True)

    MEDIA_FIELDS = [
        ("m1_name_and_placing", "M1", "Name, institution, team and final placing"),
        ("m2_quotes", "M2", "Quotes and testimonials (approved by me first)"),
        ("m3_project_description", "M3", "A short description of my team's project"),
        ("m4_portraits", "M4", "Portraits, interviews and team photos"),
        ("m5_campaign_graphics", "M5", "Campaign graphics carrying my name or team"),
        ("m6_strathmore", "M6", "Strathmore University may also use what I ticked"),
        ("m7_scesa", "M7", "SCESA may also use what I ticked"),
        ("m8_ncba", "M8", "NCBA (including NCBA Loop) may also use what I ticked"),
        ("m9_future_events", "M9", "Reuse to promote future Club events, 24 months"),
        ("m10_learning_library", "M10", "Keep my team's write-up as a learning resource"),
    ]
    RECRUITER_FIELDS = [
        ("r1_contact_details", "R1", "My name, email address and phone number"),
        ("r2_academic_details", "R2", "Institution, programme, year, GitHub or GitLab"),
        ("r3_performance_summary", "R3", "A summary of my performance and strengths"),
        ("r4_shortlist", "R4", "Include me on the recruiter shortlist"),
    ]

    class Meta:
        verbose_name = "participant consent"
        verbose_name_plural = "participant consents"

    def __str__(self):
        return f"Consent for {self.user}"

    @property
    def lanyard(self):
        """What the check-in desk issues."""
        return "Black" if self.black_lanyard else "Standard"

    @property
    def media_choices(self):
        return [
            (code, label, getattr(self, field))
            for field, code, label in self.MEDIA_FIELDS
        ]

    @property
    def recruiter_choices(self):
        return [
            (code, label, getattr(self, field))
            for field, code, label in self.RECRUITER_FIELDS
        ]

    @property
    def shares_with_recruiters(self):
        return any(getattr(self, f) for f, _, _ in self.RECRUITER_FIELDS)

    def clean(self):
        """M4 is not available with a black lanyard (clause 5.2, 5.4).

        Enforced on the model rather than only in the form: a portrait
        consent from somebody wearing a black lanyard is a contradiction
        whichever screen created it.
        """
        if self.black_lanyard and self.m4_portraits:
            raise ValidationError({
                "m4_portraits": "Portraits are not available with a black lanyard."
            })


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
        # An organiser has picked it up. Internal marker, so two reviewers
        # do not work the same registration; the team sees no difference.
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    # Roster and registration details cannot change in these states. A
    # review in progress counts: the roster must not move under the
    # organiser reading it.
    LOCKED_STATUSES = ("submitted", "under_review", "approved")

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

    # The captain's consent to be shown people looking for a team. Opt-in
    # rather than automatic: a team that has its fourth lined up offline
    # should not be handed a stranger.
    open_to_recruits = models.BooleanField(
        default=False,
        help_text="Show this team competitors who are looking for a place.",
    )
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

    def save(self, *args, **kwargs):
        from services.scoring_identity import write_identity_source
        return write_identity_source(self, lambda: super(Team, self).save(*args, **kwargs))

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
