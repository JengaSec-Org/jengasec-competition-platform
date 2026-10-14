"""Keep the published v1 columns and revoke access when source bindings disappear."""
import importlib
from types import SimpleNamespace

from django.db import migrations, models


STATEMENTS = (
    """
    CREATE OR REPLACE VIEW obs_ip_allocations_v1 WITH (security_barrier = true) AS
    SELECT 1::integer AS contract_version, ia.updated_at,
           ia.allocation_id::text AS allocation_id, host(ia.ip_address) AS ip_address,
           ia.kind, NULLIF(up.participant_identifier, '') AS participant_id,
           NULLIF(team.team_identifier, '') AS team_id,
           NULLIF(ia.team_label, '') AS team_label, NULLIF(ia.namespace, '') AS namespace,
           ia.status, ia.valid_from, ia.valid_until
    FROM ip_allocations ia
    LEFT JOIN accounts_userprofile up ON up.id = ia.participant_id
    LEFT JOIN accounts_team team ON team.id = ia.team_id
    WHERE (up.id IS NULL OR up.participant_identifier ~ '^JS26-P-[0-9]{4,}$')
      AND (team.id IS NULL OR team.team_identifier ~ '^JS[0-9]{2}-[BR]-[0-9]{3,}$')
      AND (ia.kind <> 'participant' OR ia.status = 'revoked' OR (
        ia.valid_from <= statement_timestamp()
        AND (ia.valid_until IS NULL OR ia.valid_until > statement_timestamp())
        AND team.status = 'approved'
        AND EXISTS (
          SELECT 1 FROM cyberrange_wireguardprofile wg
          JOIN auth_user account ON account.id = wg.user_id
          WHERE wg.user_id = up.user_id AND wg.team_id = ia.team_id
            AND account.is_active AND NOT wg.revoked
            AND wg.address IN (host(ia.ip_address), host(ia.ip_address) || '/32')
            AND (ia.status <> 'active' OR wg.downloaded_at IS NOT NULL)
            AND (team.captain_id = wg.user_id OR EXISTS (
              SELECT 1 FROM accounts_teammember member
              WHERE member.team_id = team.id AND member.user_id = wg.user_id
            ))
        )
      ))
    """,
    """
    CREATE OR REPLACE VIEW obs_applications_v1 WITH (security_barrier = true) AS
    SELECT 1::integer AS contract_version, app.updated_at,
           app.application_instance_id, app.enterprise, app.application, app.namespace,
           app.service_name, host(app.ip_address) AS ip_address, app.status,
           app.optional, app.enabled, NULLIF(app.image_digest, '') AS image_digest
    FROM applications app
    WHERE app.enterprise IN ('ENT-A', 'ENT-B') AND app.application ~ '^APP0[1-9]$'
      AND app.application_instance_id = app.enterprise || '/' || app.application
      AND app.namespace = 'jengasec-' || lower(app.enterprise) || '-' || lower(app.application)
    """,
    """
    CREATE OR REPLACE VIEW obs_scope_assignments_v1 WITH (security_barrier = true) AS
    SELECT 1::integer AS contract_version, scope.updated_at,
           team.team_identifier AS team_id, scope.source_cidr,
           app.enterprise, app.application, app.namespace, host(app.ip_address) AS target_ip,
           COALESCE((scope.active AND assignment.is_active AND app.enabled
            AND team.status = 'approved' AND target.status = 'approved'
            AND team.team_type = 'red' AND target.team_type = 'blue'
            AND target.track = 'application'
            AND team.competition_id = scope.competition_id
            AND target.competition_id = scope.competition_id
            AND app.competition_id = scope.competition_id
            AND assignment.red_team_id = team.id
            AND target.namespace = app.namespace
            AND target.cell_id = app.application
            AND app.enterprise = CASE target.enterprise WHEN 'a' THEN 'ENT-A' WHEN 'b' THEN 'ENT-B' END
            AND (target.service_ip IS NULL OR target.service_ip = app.ip_address)
            AND scope.valid_from <= statement_timestamp()
            AND (scope.valid_until IS NULL OR scope.valid_until > statement_timestamp())
            AND EXISTS (
              SELECT 1 FROM ip_allocations ia
              JOIN obs_ip_allocations_v1 valid ON valid.allocation_id = ia.allocation_id::text
              WHERE ia.team_id = team.id AND ia.competition_id = scope.competition_id
                AND ia.kind = 'participant' AND valid.status IN ('reserved', 'active')
                AND scope.source_cidr = network(set_masklen(ia.ip_address, 24))::text
            )), false) AS active,
           scope.valid_from, scope.valid_until
    FROM scope_assignments scope
    JOIN accounts_team team ON team.id = scope.team_id
    JOIN applications app ON app.id = scope.application_id
    JOIN competitions_targetassignment assignment ON assignment.id = scope.target_assignment_id
    JOIN accounts_team target ON target.id = assignment.target_team_id
    WHERE team.team_identifier ~ '^JS[0-9]{2}-R-[0-9]{3,}$'
    """,
    """
    CREATE OR REPLACE VIEW obs_access_principals_v1 WITH (security_barrier = true) AS
    WITH principals AS (
        SELECT team_id, user_id, created_at AS linked_at
        FROM accounts_teammember WHERE user_id IS NOT NULL
        UNION
        SELECT id AS team_id, captain_id AS user_id, updated_at AS linked_at
        FROM accounts_team WHERE captain_id IS NOT NULL
    )
    SELECT DISTINCT 1::integer AS contract_version,
        GREATEST(up.created_at, principal.linked_at, app.updated_at) AS updated_at,
        up.participant_identifier AS participant_id,
        CASE WHEN team.team_type = 'blue' THEN 'blue_team' ELSE 'red_team' END AS role,
        team.team_identifier AS team_id, app.enterprise, app.application, app.namespace
    FROM principals principal
    JOIN accounts_userprofile up ON up.user_id = principal.user_id
    JOIN auth_user account ON account.id = up.user_id AND account.is_active
    JOIN accounts_team team ON team.id = principal.team_id AND team.status = 'approved'
    JOIN obs_applications_v1 app ON app.namespace = team.namespace AND app.enabled
    JOIN applications source ON source.namespace = app.namespace AND source.competition_id = team.competition_id
    WHERE team.team_type = 'blue' AND team.track = 'application'
      AND up.role NOT IN ('admin', 'judge')
      AND team.team_identifier ~ '^JS[0-9]{2}-B-[0-9]{3,}$'
      AND up.participant_identifier ~ '^JS26-P-[0-9]{4,}$'
      AND app.application = team.cell_id
      AND app.enterprise = CASE team.enterprise WHEN 'a' THEN 'ENT-A' WHEN 'b' THEN 'ENT-B' END
      AND (team.service_ip IS NULL OR team.service_ip = source.ip_address)
    UNION
    SELECT DISTINCT 1::integer AS contract_version,
        GREATEST(up.created_at, principal.linked_at, scope.updated_at) AS updated_at,
        up.participant_identifier AS participant_id, 'red_team'::varchar AS role,
        team.team_identifier AS team_id, scope.enterprise, scope.application, scope.namespace
    FROM principals principal
    JOIN accounts_userprofile up ON up.user_id = principal.user_id
    JOIN auth_user account ON account.id = up.user_id AND account.is_active
    JOIN accounts_team team ON team.id = principal.team_id AND team.status = 'approved'
    JOIN obs_scope_assignments_v1 scope ON scope.team_id = team.team_identifier AND scope.active
    WHERE team.team_type = 'red' AND up.role NOT IN ('admin', 'judge')
      AND up.participant_identifier ~ '^JS26-P-[0-9]{4,}$'
    UNION
    SELECT 1::integer AS contract_version, up.created_at AS updated_at,
        up.participant_identifier AS participant_id,
        CASE WHEN up.role = 'admin' THEN 'staff' ELSE up.role END AS role,
        NULL::varchar AS team_id, NULL::varchar AS enterprise,
        NULL::varchar AS application, NULL::varchar AS namespace
    FROM accounts_userprofile up
    JOIN auth_user account ON account.id = up.user_id AND account.is_active
    WHERE up.role IN ('admin', 'judge') AND up.participant_identifier ~ '^JS26-P-[0-9]{4,}$'
    """,
)

FINDINGS_SQL = """
CREATE OR REPLACE VIEW obs_findings_v1 WITH (security_barrier = true) AS
SELECT 1::integer AS contract_version, finding.updated_at,
       finding.id::text AS finding_id, app.enterprise, app.application, app.namespace,
       finding.started_at AS start_ts, finding.ended_at AS end_ts,
       COALESCE((SELECT jsonb_agg(value ORDER BY value) FROM (
           SELECT DISTINCT value FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(finding.telemetry_event_ids) = 'array'
                    THEN finding.telemetry_event_ids ELSE '[]'::jsonb END
           ) ids
       ) distinct_ids), '[]'::jsonb) AS telemetry_event_ids,
       COALESCE((SELECT jsonb_agg(value ORDER BY value) FROM (
           SELECT DISTINCT value FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(finding.correlation_ids) = 'array'
                    THEN finding.correlation_ids ELSE '[]'::jsonb END
           ) ids
       ) distinct_ids), '[]'::jsonb) AS correlation_ids
FROM cyberrange_finding finding
JOIN accounts_team target ON target.id = finding.target_team_id
JOIN applications app ON app.competition_id = finding.competition_id
 AND app.enterprise = CASE target.enterprise WHEN 'a' THEN 'ENT-A' WHEN 'b' THEN 'ENT-B' END
 AND app.application = target.cell_id AND app.namespace = target.namespace
WHERE finding.ended_at >= finding.started_at
  AND jsonb_typeof(finding.telemetry_event_ids) = 'array'
  AND jsonb_typeof(finding.correlation_ids) = 'array'
  AND (finding.telemetry_event_ids <> '[]'::jsonb OR finding.correlation_ids <> '[]'::jsonb)
  AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(finding.telemetry_event_ids) = 'array'
               THEN finding.telemetry_event_ids ELSE '[]'::jsonb END
      ) ids(value)
      WHERE jsonb_typeof(value) <> 'string' OR value #>> '{}' !~ '^evt_[a-f0-9]{64}$'
  )
  AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(finding.correlation_ids) = 'array'
               THEN finding.correlation_ids ELSE '[]'::jsonb END
      ) ids(value)
      WHERE jsonb_typeof(value) <> 'string' OR value #>> '{}' !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
  )
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for statement in STATEMENTS:
        schema_editor.execute(statement)
    # Renaming keeps the existing owner and grants; CREATE OR REPLACE may append
    # columns but cannot rename those already published by the first migration.
    schema_editor.execute("ALTER VIEW obs_findings_v1 RENAME COLUMN started_at TO start_ts")
    schema_editor.execute("ALTER VIEW obs_findings_v1 RENAME COLUMN ended_at TO end_ts")
    schema_editor.execute(FINDINGS_SQL)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    original = importlib.import_module("cyberrange.migrations.0002_observability_interface")
    statements = []
    original.create_observability_views(apps, SimpleNamespace(
        connection=schema_editor.connection, execute=statements.append,
    ))
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT grantee, privilege_type, is_grantable
            FROM information_schema.table_privileges
            WHERE table_schema = current_schema() AND table_name = 'obs_findings_v1'
        """)
        grants = cursor.fetchall()
        cursor.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = 'obs_findings_v1'::regclass")
        owner = cursor.fetchone()[0]
    # Only this view changes column count. Restore its prior shape and grants
    # before Django removes correlation_ids from the backing model.
    schema_editor.execute("DROP VIEW obs_findings_v1")
    for statement in statements:
        schema_editor.execute(statement.replace("CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1))
    quote = schema_editor.quote_name
    schema_editor.execute(f"ALTER VIEW obs_findings_v1 OWNER TO {quote(owner)}")
    for grantee, privilege, grantable in grants:
        recipient = "PUBLIC" if grantee == "PUBLIC" else quote(grantee)
        option = " WITH GRANT OPTION" if grantable == "YES" else ""
        schema_editor.execute(f"GRANT {privilege} ON obs_findings_v1 TO {recipient}{option}")


class Migration(migrations.Migration):
    dependencies = [("cyberrange", "0003_merge_observability_runtime")]
    operations = [
        migrations.AddField(
            model_name="finding", name="correlation_ids",
            field=models.JSONField(blank=True, default=list,
                help_text="Opaque request or trace identifiers cited by this finding."),
        ),
        migrations.RunPython(forwards, backwards),
    ]
