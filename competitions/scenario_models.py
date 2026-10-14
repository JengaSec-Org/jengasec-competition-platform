"""Issued scenario ground truth and authenticated observation receipts."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Issued scoring records cannot be changed.")

    def delete(self):
        raise ValidationError("Issued scoring records cannot be deleted.")


class ImmutableRecord(models.Model):
    objects = ImmutableQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Issued scoring records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Issued scoring records cannot be deleted.")


class ScenarioVersion(ImmutableRecord):
    scenario = models.ForeignKey("competitions.AttackScenario", on_delete=models.PROTECT, related_name="versions")
    scenario_code = models.CharField(max_length=20)
    track = models.CharField(max_length=15)
    version = models.PositiveIntegerField()
    label = models.CharField(max_length=10, choices=[("attack", "Attack"), ("legitimate", "Legitimate")])
    family = models.CharField(max_length=30, blank=True)
    assertions = models.JSONField()
    definition_sha256 = models.CharField(max_length=64)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    issued_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scenario", "version"], name="unique_scenario_version")]


class ScoringProducer(models.Model):
    producer_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    kind = models.CharField(max_length=12, choices=[("runner", "Runner"), ("verifier", "Verifier"), ("gateway", "Gateway"), ("competitor", "Competitor"), ("deployment", "Deployment verifier")])
    team = models.ForeignKey("accounts.Team", null=True, blank=True, on_delete=models.PROTECT)
    credential_sha256 = models.CharField(max_length=64)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class ScenarioExecution(ImmutableRecord):
    case_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    objective = models.ForeignKey("competitions.CompetitionObjective", on_delete=models.PROTECT, related_name="executions")
    objective_code = models.CharField(max_length=20)
    objective_team_identifier = models.CharField(max_length=20)
    scenario_version = models.ForeignKey(ScenarioVersion, on_delete=models.PROTECT)
    opportunity_key = models.CharField(max_length=128)
    actor_team = models.ForeignKey("accounts.Team", null=True, blank=True, on_delete=models.PROTECT, related_name="issued_attacks")
    target_team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT, related_name="issued_defence_cases")
    defender_team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT, related_name="defended_scoring_cases")
    target_identity = models.ForeignKey("cyberrange.TargetIdentityInterval", on_delete=models.PROTECT)
    scope_identity = models.ForeignKey("cyberrange.ScopeAssignment", null=True, blank=True, on_delete=models.PROTECT)
    assignment_revision = models.PositiveBigIntegerField()
    starts_at = models.DateTimeField()
    deadline_at = models.DateTimeField()
    environment = models.CharField(max_length=12, choices=[("competition", "Competition"), ("rehearsal", "Rehearsal")])
    required_producers = models.JSONField(default=dict)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    issued_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["competition", "target_team", "opportunity_key"], name="unique_scoring_opportunity"),
            models.CheckConstraint(condition=models.Q(deadline_at__gt=models.F("starts_at")), name="scenario_execution_time_order"),
        ]


class ScenarioReceipt(ImmutableRecord):
    """PK is the monotonic server cursor; receipt_id is producer retry identity."""

    receipt_id = models.UUIDField()
    producer = models.ForeignKey(ScoringProducer, null=True, blank=True, on_delete=models.PROTECT)
    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    execution = models.ForeignKey(ScenarioExecution, null=True, blank=True, on_delete=models.PROTECT, related_name="receipts")
    target_identity = models.ForeignKey("cyberrange.TargetIdentityInterval", null=True, blank=True, on_delete=models.PROTECT)
    kind = models.CharField(max_length=20)
    producer_kind = models.CharField(max_length=12)
    team_identifier = models.CharField(max_length=20, blank=True)
    observed_at = models.DateTimeField()
    received_at = models.DateTimeField(default=timezone.now)
    clock_uncertainty_ms = models.PositiveIntegerField()
    measurements = models.JSONField()
    evidence_refs = models.JSONField(default=list)
    payload_sha256 = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["producer", "receipt_id"], name="unique_producer_receipt")]
        indexes = [models.Index(fields=["competition", "observed_at"], name="ix_scoring_receipt_window")]


class ScoringExclusion(ImmutableRecord):
    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    start = models.DateTimeField()
    end = models.DateTimeField()
    reason = models.CharField(max_length=30, choices=[("shared_infrastructure", "Shared infrastructure incident")])
    incident_reference = models.CharField(max_length=128)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(end__gt=models.F("start")), name="scoring_exclusion_time_order")]
