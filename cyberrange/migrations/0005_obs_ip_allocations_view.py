"""obs_ip_allocations_v1: the attribution view Observability reads.

Columns follow contracts/observability-data-interface.md (infrastructure repo):
one row per Wi-Fi claim (kind participant) and one per Blue application's
reserved address (kind application). Opaque identifiers only: no names,
emails or phone numbers. PostgreSQL only; other databases (tests, local
SQLite) skip it.

Observability's database role needs only:  GRANT SELECT ON obs_ip_allocations_v1 TO <obs role>;
"""
from django.db import migrations

VIEW = """
CREATE OR REPLACE VIEW obs_ip_allocations_v1 AS
SELECT
    'wifi-' || c.id                       AS allocation_id,
    host(c.ip_address)                    AS ip_address,
    'participant'::text                   AS kind,
    c.participant_id::text                AS participant_id,
    NULLIF(c.team_identifier, '')::text   AS team_id,
    c.team_label::text                    AS team_label,
    NULLIF(c.namespace, '')::text         AS namespace,
    CASE WHEN c.ended_at IS NULL THEN 'active' ELSE 'revoked' END::text AS status,
    c.first_seen                          AS valid_from,
    c.ended_at                            AS valid_until,
    1                                     AS contract_version,
    c.last_seen                           AS updated_at
FROM cyberrange_wificlaim c
UNION ALL
SELECT
    'app-' || t.id,
    host(t.service_ip),
    'application'::text,
    NULL::text,
    NULL::text,
    NULL::text,
    NULLIF(t.namespace, '')::text,
    'active'::text,
    t.created_at,
    NULL::timestamptz,
    1,
    t.updated_at
FROM accounts_team t
WHERE t.service_ip IS NOT NULL AND t.status = 'approved' AND t.team_type = 'blue'
"""


def create(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    from django.db import transaction

    # A view problem must never stop the site deploying: report it and carry on.
    # Re-create it later with `jengasec-manage migrate cyberrange 0004` then `migrate`.
    try:
        with transaction.atomic(using=schema_editor.connection.alias):
            schema_editor.execute(VIEW)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  WARNING: obs_ip_allocations_v1 was not created: {exc}")


def drop(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP VIEW IF EXISTS obs_ip_allocations_v1")


class Migration(migrations.Migration):
    dependencies = [
        ("cyberrange", "0004_wificlaim"),
        ("accounts", "0018_canonical_cell_ids"),
    ]

    operations = [migrations.RunPython(create, drop)]
