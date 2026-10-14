"""Privacy-safe current v1 interfaces; scoring history uses the v2 API."""
from django.db import migrations


VIEW_NAMES = (
    "obs_findings_v1", "obs_access_principals_v1", "obs_scope_assignments_v1",
    "obs_applications_v1", "obs_ip_allocations_v1",
)


def create_views(apps, schema_editor):
    postgres = schema_editor.connection.vendor == "postgresql"
    barrier = " WITH (security_barrier = true)" if postgres else ""
    address = "host(ia.ip_address)" if postgres else "ia.ip_address"
    app_address = "host(app.ip_address)" if postgres else "app.ip_address"
    scope_address = "host(scope.target_ip)" if postgres else "scope.target_ip"
    application_codes = "('APP01','APP02','APP03','APP04','APP05','APP06','APP07','APP08','APP09')"
    definitions = {
        "obs_ip_allocations_v1": f"""
            SELECT 1 AS contract_version, ia.updated_at,
                   CAST(ia.allocation_id AS text) AS allocation_id,
                   {address} AS ip_address, ia.kind,
                   NULLIF(ia.participant_identifier, '') AS participant_id,
                   NULLIF(ia.team_identifier, '') AS team_id,
                   NULLIF(ia.team_label, '') AS team_label,
                   NULLIF(ia.namespace, '') AS namespace,
                   ia.status, ia.valid_from, ia.valid_until
            FROM ip_allocations ia WHERE ia.valid_until IS NULL
        """,
        "obs_applications_v1": f"""
            SELECT 1 AS contract_version, app.updated_at,
                   app.application_instance_id, app.enterprise, app.application,
                   app.namespace, app.service_name, {app_address} AS ip_address,
                   app.status, app.optional, app.enabled,
                   (SELECT identity.frozen_digest FROM cyberrange_targetidentityinterval identity
                    WHERE identity.range_target_id = app.id AND identity.team_id = app.team_id
                    ORDER BY identity.valid_from DESC, identity.id DESC LIMIT 1) AS image_digest
            FROM applications app WHERE app.application IN {application_codes}
        """,
        "obs_scope_assignments_v1": f"""
            SELECT 1 AS contract_version, scope.updated_at, scope.team_identifier AS team_id,
                   scope.source_cidr, scope.enterprise, scope.application_code AS application,
                   scope.namespace, {scope_address} AS target_ip, scope.active,
                   scope.valid_from, scope.valid_until
            FROM scope_assignments scope
            WHERE scope.valid_until IS NULL AND scope.application_code IN {application_codes}
        """,
        "obs_access_principals_v1": f"""
            WITH principals AS (
                SELECT team_id, user_id, created_at AS linked_at FROM accounts_teammember
                WHERE user_id IS NOT NULL
                UNION
                SELECT id AS team_id, captain_id AS user_id, updated_at AS linked_at
                FROM accounts_team WHERE captain_id IS NOT NULL
            )
            SELECT DISTINCT 1 AS contract_version, app.updated_at,
                   profile.participant_identifier AS participant_id, 'blue_team' AS role,
                   team.team_identifier AS team_id, app.enterprise, app.application, app.namespace
            FROM principals principal
            JOIN accounts_userprofile profile ON profile.user_id = principal.user_id
            JOIN auth_user auth ON auth.id = profile.user_id
            JOIN accounts_team team ON team.id = principal.team_id
            JOIN applications app ON app.team_id = team.id AND app.competition_id = team.competition_id
            WHERE team.team_type = 'blue' AND team.status = 'approved' AND auth.is_active
              AND profile.participant_identifier <> '' AND app.enabled
              AND app.application IN {application_codes}
            UNION
            SELECT DISTINCT 1 AS contract_version, scope.updated_at,
                   profile.participant_identifier AS participant_id,
                   CASE team.team_type WHEN 'red' THEN 'red_team' ELSE 'blue_team' END AS role,
                   scope.team_identifier AS team_id, scope.enterprise,
                   scope.application_code AS application, scope.namespace
            FROM principals principal
            JOIN accounts_userprofile profile ON profile.user_id = principal.user_id
            JOIN auth_user auth ON auth.id = profile.user_id
            JOIN accounts_team team ON team.id = principal.team_id
            JOIN scope_assignments scope ON scope.team_id = team.id
            WHERE team.status = 'approved' AND auth.is_active
              AND profile.participant_identifier <> '' AND scope.active AND scope.valid_until IS NULL
              AND scope.application_code IN {application_codes}
            UNION
            SELECT 1 AS contract_version, profile.created_at AS updated_at,
                   profile.participant_identifier AS participant_id,
                   CASE WHEN auth.is_staff THEN 'staff' ELSE 'judge' END AS role,
                   NULL AS team_id, NULL AS enterprise, NULL AS application, NULL AS namespace
            FROM accounts_userprofile profile JOIN auth_user auth ON auth.id = profile.user_id
            WHERE auth.is_active AND (auth.is_staff OR profile.role = 'judge')
              AND profile.participant_identifier <> ''
        """,
        "obs_findings_v1": f"""
            SELECT 1 AS contract_version, finding.updated_at,
                   CAST(finding.id AS text) AS finding_id,
                   identity.enterprise, identity.application, identity.namespace,
                   finding.started_at AS start_ts, finding.ended_at AS end_ts,
                   finding.telemetry_event_ids, finding.correlation_ids
            FROM cyberrange_finding finding
            JOIN cyberrange_targetidentityinterval identity
              ON identity.competition_id = finding.competition_id
             AND identity.team_id = finding.target_team_id
             AND identity.valid_from <= finding.started_at
             AND identity.valid_until > finding.ended_at
            WHERE identity.application IN {application_codes}
              AND (CAST(finding.telemetry_event_ids AS text) <> '[]'
                   OR CAST(finding.correlation_ids AS text) <> '[]')
              AND NOT EXISTS (
                  SELECT 1 FROM cyberrange_targetidentityinterval other
                  WHERE other.id <> identity.id AND other.competition_id = identity.competition_id
                    AND other.team_id = identity.team_id
                    AND other.valid_from <= finding.ended_at
                    AND other.valid_until > finding.started_at
              )
        """,
    }
    for name in reversed(VIEW_NAMES):
        schema_editor.execute(f"CREATE VIEW {name}{barrier} AS {definitions[name]}")


def drop_views(apps, schema_editor):
    for name in VIEW_NAMES:
        schema_editor.execute(f"DROP VIEW IF EXISTS {name}")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_userprofile_participant_identifier"),
        ("cyberrange", "0002_finding_correlation_ids_finding_scoring_objective_and_more"),
    ]

    operations = [migrations.RunPython(create_views, drop_views)]
