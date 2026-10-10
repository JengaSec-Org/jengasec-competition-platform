# Observability data interface

The competition platform is authoritative for participant allocation,
application inventory, attack scope, dashboard access and finding references.
Observability receives only the minimum operational fields through versioned,
read-only PostgreSQL views.

The shared column contract lives in
`jengasec-cyberrange-infrastructure/contracts/observability-data-interface.md`.
Changing a view name, column or meaning requires the contract review before a
new view version is published.

## Reconcile data

Apply migrations, then reconcile one competition:

```bash
python manage.py migrate
python manage.py sync_observability_data --competition 1
```

The command refuses to publish an incomplete application catalogue. APP01
through APP09 must exist as Application Briefs. It creates an ENT-A and ENT-B
instance for each brief. APP08 and APP09 remain disabled unless the organiser
enables both replicas deliberately:

```bash
python manage.py sync_observability_data --competition 1 --enable-optional
```

Reconciliation is idempotent. Run it after participant, WireGuard, target,
brief or application state changes. A WireGuard address is `reserved` before
download, `active` after download, and `revoked` after revocation. Network
handshake state is not inferred by the platform.

## Read-only views

- `obs_ip_allocations_v1`
- `obs_applications_v1`
- `obs_scope_assignments_v1`
- `obs_access_principals_v1`
- `obs_findings_v1`

Every view includes `contract_version` and `updated_at`. The access view uses
opaque `JS26-P-NNNN` participant identifiers. Team names, participant names,
usernames, email addresses, phone numbers, identity documents, finding prose,
attachments and WireGuard configuration never appear in these views.

A null enterprise/application/namespace in the access view means a judge or
staff principal has competition-wide read scope. Blue-team rows are limited to
their own application. Red-team rows are limited to assigned applications;
the `active` field in the scope view remains the enforcement source for the
current engagement window.

## Database access

Create the login and password through the approved secret-management process.
As a PostgreSQL owner, restrict it to the five views:

```sql
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM jengasec_obs_reader;
GRANT CONNECT ON DATABASE jengasec TO jengasec_obs_reader;
GRANT USAGE ON SCHEMA public TO jengasec_obs_reader;
GRANT SELECT ON
  obs_ip_allocations_v1,
  obs_applications_v1,
  obs_scope_assignments_v1,
  obs_access_principals_v1,
  obs_findings_v1
TO jengasec_obs_reader;
```

Keep the TLS-required connection string in the deployment secret store. Do
not put it in source control, generated exports or application logs. Confirm
the role cannot select `accounts_user`, `accounts_userprofile`,
`accounts_teammember`, `cyberrange_finding` or `cyberrange_wireguardprofile`.

## Validation and rollback

Hosted CI creates PostgreSQL, applies every migration, runs the full test
suite, checks the exact access-view columns and proves both app-scoped and
competition-wide rows. Before production use, a second operator should run:

```bash
python manage.py sync_observability_data --competition 1
python manage.py test cyberrange
```

Rollback to `cyberrange 0001` drops the five views before their backing tables.
Take a database backup first. The operation does not deploy to the OBS VM or
Server 3.

The AI telemetry destination, authentication and API remain AI-owned external
dependencies. This interface does not invent them.
