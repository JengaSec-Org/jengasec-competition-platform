"""Competition lifecycle: competitions, categories, and phases.

Schema reference: JengaSec Database Design V1, section 4 (competitions).
"""
from django.conf import settings
from django.db import models

from .constants import Enterprise, Track  # noqa: F401  (re-exported for convenience)


class Competition(models.Model):
    class Status(models.TextChoices):
        """Administrative lifecycle of the competition record."""

        DRAFT = "draft", "Draft"
        OPEN = "open", "Open for Registration"
        IN_PROGRESS = "in_progress", "In Progress"
        JUDGING = "judging", "Judging"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    class Phase(models.TextChoices):
        """Where the competition has actually got to.

        Distinct from `status`, which is the admin lifecycle. `phase`
        drives which dashboard a team lands on.
        """

        REGISTRATION = "registration", "Registration"
        BUILD = "build", "Build"
        LIVE = "live", "Live Competition"
        JUDGING = "judging", "Judging"

    # Order teams progress through. Judging is deliberately excluded from
    # the team-facing rail — it is organiser-side.
    PHASE_ORDER = [Phase.REGISTRATION, Phase.BUILD, Phase.LIVE, Phase.JUDGING]
    TEAM_PHASES = [Phase.REGISTRATION, Phase.BUILD, Phase.LIVE]

    name = models.CharField(max_length=200, unique=True)
    theme = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    phase = models.CharField(
        max_length=20,
        choices=Phase.choices,
        default=Phase.REGISTRATION,
        help_text="Drives which dashboard teams land on.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="competitions_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="ck_competition_dates",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def is_open_for_submissions(self):
        """Whether documents may be uploaded right now.

        The lifecycle status sets the baseline; an organiser can close the
        window early from the Competition Management screen without having
        to change the status or the phase.
        """
        if self.status not in (self.Status.OPEN, self.Status.IN_PROGRESS):
            return False
        return self.settings.submissions_open

    @property
    def settings(self):
        """Per-competition settings, created on first access.

        A property rather than a required relation so competitions that
        predate the settings table keep working untouched.
        """
        settings_obj = getattr(self, "_settings_cache", None)
        if settings_obj is None:
            settings_obj, _ = CompetitionSettings.objects.get_or_create(
                competition=self
            )
            self._settings_cache = settings_obj
        return settings_obj

    @property
    def phase_index(self):
        try:
            return self.PHASE_ORDER.index(self.phase)
        except ValueError:
            return 0

    def phase_reached(self, phase):
        """True once the competition has got to `phase` (or past it).

        Used for the read-only "look back" at earlier phase dashboards.
        """
        try:
            return self.phase_index >= self.PHASE_ORDER.index(phase)
        except ValueError:
            return False

    @property
    def registration_open(self):
        return self.phase == self.Phase.REGISTRATION


class CompetitionSettings(models.Model):
    """Per-competition switches and deadlines (schema: Competition_Settings).

    Kept apart from Competition so the lifecycle controls an organiser
    flips during the event don't churn the competition record itself.
    """

    competition = models.OneToOneField(
        Competition, on_delete=models.CASCADE, related_name="settings_row"
    )

    # Lifecycle switches, driven from the Competition Management screen.
    submissions_open = models.BooleanField(
        default=True, help_text="Teams may upload documents."
    )
    judging_open = models.BooleanField(
        default=False, help_text="Judges may score submissions."
    )
    results_published = models.BooleanField(
        default=False, help_text="Teams can see final scores and feedback."
    )

    # Timelines.
    registration_deadline = models.DateTimeField(null=True, blank=True)
    build_deadline = models.DateTimeField(null=True, blank=True)
    submission_deadline = models.DateTimeField(null=True, blank=True)

    # Caps.
    max_proposals_per_team = models.PositiveIntegerField(default=1)
    default_proposal_cap = models.PositiveIntegerField(
        default=20, help_text="Applied to new application briefs."
    )
    appeal_window_days = models.PositiveIntegerField(default=3)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "competition settings"

    def __str__(self):
        return f"Settings for {self.competition.name}"


class CompetitionCategory(models.Model):
    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="categories"
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "competition categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "name"], name="unique_category_per_competition"
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.competition.name})"


class ApplicationBrief(models.Model):
    """An application JengaSec has lined up for blue teams to build.

    Teams register by proposing against a brief. Several teams may
    propose for the same application — the cap keeps any one brief from
    swallowing the field — and the best proposals are picked later
    through the normal rubric/evaluation machinery.
    """

    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="application_briefs"
    )
    code = models.CharField(max_length=20, help_text="e.g. APP-A1")
    name = models.CharField(max_length=200)
    enterprise = models.CharField(max_length=1, choices=Enterprise.choices, blank=True)
    track = models.CharField(
        max_length=15, choices=Track.choices, default=Track.APPLICATION
    )
    summary = models.TextField(blank=True)
    requirements = models.TextField(
        blank=True, help_text="One requirement per line."
    )
    proposal_cap = models.PositiveIntegerField(
        default=20, help_text="Maximum proposals accepted for this application."
    )
    is_open = models.BooleanField(default=True)

    class Meta:
        ordering = ["enterprise", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "code"], name="unique_brief_per_competition"
            )
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"

    @property
    def proposal_count(self):
        return self.proposals.count()

    @property
    def is_full(self):
        return self.proposal_count >= self.proposal_cap

    @property
    def places_left(self):
        return max(self.proposal_cap - self.proposal_count, 0)

    @property
    def requirement_list(self):
        return [line.strip() for line in self.requirements.splitlines() if line.strip()]


class AttackScenario(models.Model):
    """A predefined, controlled attack scenario.

    Final Competition Overview §8: don't try to understand every possible
    attack — score against scenarios with explicit success conditions, so
    results are objective, reproducible and disputable on evidence.
    """

    code = models.CharField(max_length=20, unique=True, help_text="e.g. APP-01")
    track = models.CharField(max_length=15, choices=Track.choices)
    name = models.CharField(max_length=200)
    objective = models.TextField()
    expected_evidence = models.TextField(blank=True)
    success_condition = models.TextField()
    red_points = models.IntegerField(default=0)
    # Negative: what the defending blue team loses on full compromise.
    blue_points = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["track", "code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class TargetAssignment(models.Model):
    """Which blue team a red team is cleared to attack (§22: 20 Blue × 2 Red)."""

    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="target_assignments"
    )
    red_team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="target_assignments"
    )
    target_team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="attacker_assignments"
    )
    endpoint = models.CharField(
        max_length=255, blank=True, help_text="In-scope endpoint or address."
    )
    access_notes = models.TextField(blank=True, help_text="VPN profile, credentials, caveats.")
    is_active = models.BooleanField(default=False, help_text="Engagement window open.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["red_team", "target_team"]
        constraints = [
            models.UniqueConstraint(
                fields=["red_team", "target_team"], name="unique_target_assignment"
            )
        ]

    def __str__(self):
        return f"{self.red_team.cell_id or self.red_team.team_name} → {self.target_team.cell_id or self.target_team.team_name}"


class CompetitionObjective(models.Model):
    """A scored objective issued to one team (§17)."""

    code = models.CharField(max_length=20, help_text="e.g. APP-07")
    team = models.ForeignKey(
        "accounts.Team", on_delete=models.CASCADE, related_name="objectives"
    )
    name = models.CharField(max_length=200)
    objective = models.TextField(blank=True)
    max_points = models.PositiveIntegerField(default=100)
    success_conditions = models.JSONField(
        default=list, blank=True, help_text="List of condition strings."
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "code"], name="unique_objective_per_team"
            )
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"


class CompetitionPhase(models.Model):
    competition = models.ForeignKey(
        Competition, on_delete=models.CASCADE, related_name="phases"
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["competition", "name"], name="unique_phase_per_competition"
            )
        ]

    def __str__(self):
        return f"{self.competition.name} :: {self.name}"
