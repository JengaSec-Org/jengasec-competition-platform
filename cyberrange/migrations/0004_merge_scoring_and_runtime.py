from django.db import migrations


def application_view(schema_editor, *, require_reservation):
    postgres = schema_editor.connection.vendor == "postgresql"
    create = "CREATE OR REPLACE VIEW" if postgres else "CREATE VIEW"
    barrier = " WITH (security_barrier = true)" if postgres else ""
    address = "host(app.ip_address)" if postgres else "app.ip_address"
    if not postgres:
        schema_editor.execute("DROP VIEW IF EXISTS obs_applications_v1")
    owner = """
        JOIN accounts_team owner ON owner.id = app.team_id
                                AND owner.competition_id = app.competition_id
    """ if require_reservation else ""
    matches = "AND owner.service_ip IS NOT NULL AND owner.service_ip = app.ip_address" if require_reservation else ""
    schema_editor.execute(f"""
        {create} obs_applications_v1{barrier} AS
        SELECT 1 AS contract_version, app.updated_at,
               app.application_instance_id, app.enterprise, app.application,
               app.namespace, app.service_name, {address} AS ip_address,
               app.status, app.optional, app.enabled,
               (SELECT identity.frozen_digest FROM cyberrange_targetidentityinterval identity
                WHERE identity.range_target_id = app.id AND identity.team_id = app.team_id
                ORDER BY identity.valid_from DESC, identity.id DESC LIMIT 1) AS image_digest
        FROM applications app {owner}
        WHERE app.application IN ('APP01','APP02','APP03','APP04','APP05','APP06','APP07','APP08','APP09')
        {matches}
    """)


def require_service_reservation(apps, schema_editor):
    application_view(schema_editor, require_reservation=True)


def restore_registered_address(apps, schema_editor):
    application_view(schema_editor, require_reservation=False)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0018_merge_policy_and_runtime"),
        ("cyberrange", "0002_wireguard_runtime_state"),
        ("cyberrange", "0003_observability_views"),
    ]

    operations = [migrations.RunPython(require_service_reservation, restore_registered_address)]
