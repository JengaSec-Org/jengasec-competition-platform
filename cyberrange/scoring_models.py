"""Canonical source records and event-time scoring attribution.

Current range inventory is kept separately from the intervals used by scores.
Only the identity service advances intervals; historical identity fields do
not follow later edits to a team, allocation or deployment.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class IntervalQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Use the identity service to advance an interval.")

    def delete(self):
        raise ValidationError("Attribution history cannot be deleted.")


class IdentityInterval(models.Model):
    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    revision = models.PositiveBigIntegerField()
    valid_from = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    objects = IntervalQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Attribution history cannot be overwritten.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Attribution history cannot be deleted.")


class ScoringIdentityState(models.Model):
    competition = models.OneToOneField(
        "competitions.Competition", on_delete=models.PROTECT, related_name="scoring_identity"
    )
    revision = models.PositiveBigIntegerField(default=0)
    baseline_at = models.DateTimeField(default=timezone.now)
    refreshed_at = models.DateTimeField(default=timezone.now)
    source_sha256 = models.CharField(max_length=64, blank=True)


class TeamIdentityInterval(IdentityInterval):
    team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT)
    identifier = models.CharField(max_length=20)
    side = models.CharField(max_length=10)
    track = models.CharField(max_length=15)
    enterprise = models.CharField(max_length=5)
    cell_id = models.CharField(max_length=20)
    namespace = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["competition", "team"], condition=models.Q(valid_until=None), name="one_open_team_identity"),
            models.CheckConstraint(condition=models.Q(valid_until=None) | models.Q(valid_until__gte=models.F("valid_from")), name="team_identity_time_order"),
        ]
        indexes = [models.Index(fields=["competition", "identifier", "valid_from"], name="ix_team_identity_time")]


class RangeApplication(models.Model):
    """An explicitly registered target; APP rows also supply the OBS v1 view."""

    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT, related_name="range_applications")
    team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT, related_name="range_applications")
    application_instance_id = models.CharField(max_length=30)
    enterprise = models.CharField(max_length=5, choices=[("ENT-A", "ENT-A"), ("ENT-B", "ENT-B")])
    application = models.CharField(max_length=20)
    namespace = models.CharField(max_length=80)
    service_name = models.CharField(max_length=200)
    ip_address = models.GenericIPAddressField()
    port = models.PositiveIntegerField(default=8080, validators=[MinValueValidator(1), MaxValueValidator(65535)])
    protocol = models.CharField(max_length=3, default="tcp", choices=[("tcp", "TCP"), ("udp", "UDP")])
    status = models.CharField(max_length=10, default="building", choices=[("building", "Building"), ("frozen", "Frozen"), ("live", "Live"), ("down", "Down")])
    optional = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    image_digest = models.CharField(max_length=71, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "applications"
        constraints = [
            models.UniqueConstraint(fields=["competition", "application_instance_id"], name="unique_application_instance"),
            models.UniqueConstraint(fields=["competition", "namespace"], name="unique_application_namespace"),
            models.UniqueConstraint(fields=["competition", "ip_address", "port", "protocol"], name="unique_target_tuple"),
        ]

    def clean(self):
        from services.scoring_identity import validate_target_source
        validate_target_source(self)

    def save(self, *args, **kwargs):
        from services.scoring_identity import write_identity_source
        self.full_clean()
        return write_identity_source(self, lambda: super(RangeApplication, self).save(*args, **kwargs))


class TargetIdentityInterval(IdentityInterval):
    range_target = models.ForeignKey(RangeApplication, on_delete=models.PROTECT, related_name="identity_intervals")
    team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT)
    team_identifier = models.CharField(max_length=20)
    target_id = models.CharField(max_length=30)
    enterprise = models.CharField(max_length=5)
    application = models.CharField(max_length=20)
    namespace = models.CharField(max_length=80)
    ip_address = models.GenericIPAddressField()
    port = models.PositiveIntegerField()
    protocol = models.CharField(max_length=3)
    frozen_digest = models.CharField(max_length=71)
    frozen_extra_digests = models.JSONField(null=True, blank=True, default=None)
    verified_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["competition", "team", "valid_from"], name="ix_target_identity_time")]
        constraints = [models.CheckConstraint(condition=models.Q(valid_until__gt=models.F("valid_from")), name="target_identity_time_order")]


class IPAllocation(IdentityInterval):
    allocation_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    ip_address = models.GenericIPAddressField()
    kind = models.CharField(max_length=12, choices=[("participant", "Participant"), ("application", "Application"), ("spare", "Spare")])
    participant = models.ForeignKey("accounts.UserProfile", null=True, blank=True, on_delete=models.PROTECT)
    participant_identifier = models.CharField(max_length=20, blank=True)
    team = models.ForeignKey("accounts.Team", null=True, blank=True, on_delete=models.PROTECT)
    team_identifier = models.CharField(max_length=20, blank=True)
    team_label = models.CharField(max_length=10, blank=True)
    namespace = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, default="active")

    class Meta:
        db_table = "ip_allocations"
        constraints = [
            models.UniqueConstraint(fields=["competition", "ip_address"], condition=models.Q(valid_until=None), name="one_open_ip_allocation"),
            models.CheckConstraint(condition=models.Q(valid_until=None) | models.Q(valid_until__gte=models.F("valid_from")), name="ip_allocation_time_order"),
        ]


class ActionScope(models.Model):
    """An organiser-issued permission for an AI defender, never a team claim."""

    competition = models.ForeignKey("competitions.Competition", on_delete=models.PROTECT)
    team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT)
    target = models.ForeignKey(RangeApplication, on_delete=models.PROTECT)
    allowed_actions = models.JSONField(default=list)
    source_cidrs = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    approved_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["competition", "team", "target"], name="unique_defender_action_scope")]

    def save(self, *args, **kwargs):
        import ipaddress
        from services.scoring_identity import ACTIONS, write_identity_source
        if (not self.approved_by.is_staff or not self.approved_by.is_active
                or self.team.team_type != "blue" or self.team.track != "ai"
                or self.team.competition_id != self.competition_id
                or self.target.competition_id != self.competition_id
                or not isinstance(self.allowed_actions, list)
                or any(not isinstance(action, str) or action not in ACTIONS for action in self.allowed_actions)
                or len(set(self.allowed_actions)) != len(self.allowed_actions)):
            raise ValidationError("Action scope must name an organiser-approved team, target and capabilities.")
        if not isinstance(self.source_cidrs, list) or len(self.source_cidrs) > 256:
            raise ValidationError("Action traffic sources must be a bounded list of host networks.")
        for value in self.source_cidrs:
            try:
                network = ipaddress.ip_network(value, strict=True)
                if not isinstance(value, str) or "%" in value or network.prefixlen != network.max_prefixlen:
                    raise ValueError()
            except (TypeError, ValueError) as exc:
                raise ValidationError("Action traffic sources must use exact IPv4 or IPv6 host networks.") from exc
        return write_identity_source(self, lambda: super(ActionScope, self).save(*args, **kwargs))


class ScopeAssignment(IdentityInterval):
    scope_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    target_assignment = models.ForeignKey("competitions.TargetAssignment", null=True, blank=True, on_delete=models.SET_NULL)
    action_scope = models.ForeignKey(ActionScope, null=True, blank=True, on_delete=models.PROTECT)
    team = models.ForeignKey("accounts.Team", on_delete=models.PROTECT)
    team_identifier = models.CharField(max_length=20)
    source_cidr = models.CharField(max_length=43)
    application = models.ForeignKey(RangeApplication, on_delete=models.PROTECT)
    target_id = models.CharField(max_length=30)
    target_team_identifier = models.CharField(max_length=20)
    defender_team_identifier = models.CharField(max_length=20)
    enterprise = models.CharField(max_length=5)
    application_code = models.CharField(max_length=20)
    namespace = models.CharField(max_length=80)
    target_ip = models.GenericIPAddressField()
    allowed_actions = models.JSONField(default=list)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "scope_assignments"
        indexes = [models.Index(fields=["competition", "team", "valid_from"], name="ix_scope_identity_time")]
        constraints = [models.CheckConstraint(condition=models.Q(valid_until=None) | models.Q(valid_until__gte=models.F("valid_from")), name="scope_identity_time_order")]
