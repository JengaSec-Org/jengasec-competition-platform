import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


VIEW_NAMES = (
    "obs_findings_v1",
    "obs_access_principals_v1",
    "obs_scope_assignments_v1",
    "obs_applications_v1",
    "obs_ip_allocations_v1",
)


def create_observability_views(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    statements = (
        """
        CREATE VIEW obs_ip_allocations_v1 WITH (security_barrier = true) AS
        SELECT
            1::integer AS contract_version,
            ia.updated_at,
            ia.allocation_id::text AS allocation_id,
            host(ia.ip_address) AS ip_address,
            ia.kind,
            NULLIF(up.participant_identifier, '') AS participant_id,
            NULLIF(team.team_identifier, '') AS team_id,
            NULLIF(ia.team_label, '') AS team_label,
            NULLIF(ia.namespace, '') AS namespace,
            ia.status,
            ia.valid_from,
            ia.valid_until
        FROM ip_allocations ia
        LEFT JOIN accounts_userprofile up ON up.id = ia.participant_id
        LEFT JOIN accounts_team team ON team.id = ia.team_id
        """,
        """
        CREATE VIEW obs_applications_v1 WITH (security_barrier = true) AS
        SELECT
            1::integer AS contract_version,
            app.updated_at,
            app.application_instance_id,
            app.enterprise,
            app.application,
            app.namespace,
            app.service_name,
            host(app.ip_address) AS ip_address,
            app.status,
            app.optional,
            app.enabled,
            NULLIF(app.image_digest, '') AS image_digest
        FROM applications app
        """,
        """
        CREATE VIEW obs_scope_assignments_v1 WITH (security_barrier = true) AS
        SELECT
            1::integer AS contract_version,
            scope.updated_at,
            team.team_identifier AS team_id,
            scope.source_cidr,
            app.enterprise,
            app.application,
            app.namespace,
            host(app.ip_address) AS target_ip,
            scope.active,
            scope.valid_from,
            scope.valid_until
        FROM scope_assignments scope
        JOIN accounts_team team ON team.id = scope.team_id
        JOIN applications app ON app.id = scope.application_id
        """,
        """
        CREATE VIEW obs_access_principals_v1 WITH (security_barrier = true) AS
        WITH principals AS (
            SELECT team_id, user_id, created_at AS linked_at
            FROM accounts_teammember
            WHERE user_id IS NOT NULL
            UNION
            SELECT id AS team_id, captain_id AS user_id, updated_at AS linked_at
            FROM accounts_team
            WHERE captain_id IS NOT NULL
        )
        SELECT DISTINCT
            1::integer AS contract_version,
            GREATEST(up.created_at, principal.linked_at, app.updated_at) AS updated_at,
            up.participant_identifier AS participant_id,
            CASE WHEN team.team_type = 'blue' THEN 'blue_team' ELSE 'red_team' END AS role,
            team.team_identifier AS team_id,
            app.enterprise,
            app.application,
            app.namespace
        FROM principals principal
        JOIN accounts_userprofile up ON up.user_id = principal.user_id
        JOIN accounts_team team ON team.id = principal.team_id
        JOIN applications app
          ON app.competition_id = team.competition_id
         AND app.enterprise = CASE team.enterprise WHEN 'a' THEN 'ENT-A' WHEN 'b' THEN 'ENT-B' END
         AND app.application = team.cell_id
        WHERE team.team_type = 'blue' AND up.participant_identifier <> ''
        UNION
        SELECT DISTINCT
            1::integer AS contract_version,
            GREATEST(up.created_at, principal.linked_at, scope.updated_at) AS updated_at,
            up.participant_identifier AS participant_id,
            'red_team'::varchar AS role,
            team.team_identifier AS team_id,
            app.enterprise,
            app.application,
            app.namespace
        FROM principals principal
        JOIN accounts_userprofile up ON up.user_id = principal.user_id
        JOIN accounts_team team ON team.id = principal.team_id
        JOIN scope_assignments scope ON scope.team_id = team.id
        JOIN applications app ON app.id = scope.application_id
        WHERE team.team_type = 'red' AND up.participant_identifier <> ''
        UNION
        SELECT
            1::integer AS contract_version,
            up.created_at AS updated_at,
            up.participant_identifier AS participant_id,
            CASE WHEN up.role = 'admin' THEN 'staff' ELSE up.role END AS role,
            NULL::varchar AS team_id,
            NULL::varchar AS enterprise,
            NULL::varchar AS application,
            NULL::varchar AS namespace
        FROM accounts_userprofile up
        WHERE up.role IN ('admin', 'judge') AND up.participant_identifier <> ''
        """,
        """
        CREATE VIEW obs_findings_v1 WITH (security_barrier = true) AS
        SELECT
            1::integer AS contract_version,
            finding.updated_at,
            finding.id::text AS finding_id,
            app.enterprise,
            app.application,
            app.namespace,
            finding.started_at,
            finding.ended_at,
            finding.telemetry_event_ids
        FROM cyberrange_finding finding
        JOIN accounts_team target ON target.id = finding.target_team_id
        JOIN applications app
          ON app.competition_id = finding.competition_id
         AND app.enterprise = CASE target.enterprise WHEN 'a' THEN 'ENT-A' WHEN 'b' THEN 'ENT-B' END
         AND app.application = target.cell_id
        """,
    )
    for statement in statements:
        schema_editor.execute(statement)


def drop_observability_views(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for name in VIEW_NAMES:
        schema_editor.execute(f"DROP VIEW IF EXISTS {name}")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_userprofile_participant_identifier"),
        ("competitions", "0017_competitionsettings_red_roster_deadline"),
        ("cyberrange", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="finding",
            name="telemetry_event_ids",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Stable evt_ identifiers cited by this finding; no request or response bodies.",
            ),
        ),
        migrations.CreateModel(
            name="RangeApplication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("application_instance_id", models.CharField(max_length=11)),
                ("enterprise", models.CharField(choices=[("ENT-A", "ENT-A"), ("ENT-B", "ENT-B")], max_length=5)),
                ("application", models.CharField(max_length=5)),
                ("namespace", models.CharField(max_length=80)),
                ("service_name", models.CharField(max_length=200)),
                ("ip_address", models.GenericIPAddressField()),
                ("status", models.CharField(choices=[("building", "Building"), ("frozen", "Frozen"), ("live", "Live"), ("down", "Down")], default="building", max_length=10)),
                ("optional", models.BooleanField(default=False)),
                ("enabled", models.BooleanField(default=True)),
                ("image_digest", models.CharField(blank=True, help_text="Immutable sha256 image digest; empty until the application is frozen.", max_length=71)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("competition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="range_applications", to="competitions.competition")),
            ],
            options={"db_table": "applications", "ordering": ["enterprise", "application"]},
        ),
        migrations.CreateModel(
            name="IPAllocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allocation_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("ip_address", models.GenericIPAddressField()),
                ("kind", models.CharField(choices=[("participant", "Participant"), ("application", "Application"), ("spare", "Spare")], max_length=12)),
                ("team_label", models.CharField(blank=True, choices=[("red-app", "Red application"), ("blue-app", "Blue application"), ("red-ai", "Red AI"), ("blue-ai", "Blue AI"), ("staff", "Staff"), ("judge", "Judge")], max_length=10)),
                ("namespace", models.CharField(blank=True, max_length=80)),
                ("status", models.CharField(choices=[("reserved", "Reserved"), ("active", "Active"), ("revoked", "Revoked")], default="reserved", max_length=10)),
                ("valid_from", models.DateTimeField(default=django.utils.timezone.now)),
                ("valid_until", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("competition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ip_allocations", to="competitions.competition")),
                ("participant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ip_allocations", to="accounts.userprofile")),
                ("team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ip_allocations", to="accounts.team")),
            ],
            options={"db_table": "ip_allocations", "ordering": ["ip_address"]},
        ),
        migrations.CreateModel(
            name="ScopeAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_cidr", models.CharField(max_length=43)),
                ("active", models.BooleanField(default=False)),
                ("valid_from", models.DateTimeField(default=django.utils.timezone.now)),
                ("valid_until", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("application", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="scope_assignments", to="cyberrange.rangeapplication")),
                ("competition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="range_scopes", to="competitions.competition")),
                ("target_assignment", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="range_scope", to="competitions.targetassignment")),
                ("team", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="range_scopes", to="accounts.team")),
            ],
            options={"db_table": "scope_assignments", "ordering": ["team__team_identifier", "application__application_instance_id"]},
        ),
        migrations.AddConstraint(
            model_name="rangeapplication",
            constraint=models.UniqueConstraint(fields=("competition", "application_instance_id"), name="unique_application_instance"),
        ),
        migrations.AddConstraint(
            model_name="rangeapplication",
            constraint=models.UniqueConstraint(fields=("competition", "namespace"), name="unique_application_namespace"),
        ),
        migrations.AddConstraint(
            model_name="rangeapplication",
            constraint=models.UniqueConstraint(fields=("competition", "ip_address"), name="unique_application_ip"),
        ),
        migrations.AddConstraint(
            model_name="ipallocation",
            constraint=models.UniqueConstraint(fields=("competition", "ip_address"), name="unique_competition_ip_allocation"),
        ),
        migrations.RunPython(create_observability_views, drop_observability_views),
    ]
