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
instance for each brief. New APP08 and APP09 instances start disabled. Later
runs preserve activation, runtime status and frozen image digests. To enable
both replicas deliberately:

```bash
python manage.py sync_observability_data --competition 1 --enable-optional
```

Reconciliation is transactional and repeatable: unchanged records keep their
identifiers and timestamps. A default rerun does not disable optional apps;
inconsistent activation between replicas fails without partial updates.

An assigned `Team.service_ip` takes precedence over the existing application
reservation, followed by the shared address-plan reservation for a new app.
Conflicting addresses or noncanonical namespaces fail explicitly. The command
does not rewrite a team's assigned namespace. A missing namespace creates no
application read scope.

Run reconciliation after participant, WireGuard, target, brief or application
state changes. Participant addresses and Red-team networks come from existing
WireGuard assignments for active accounts in approved teams, never team order.
An address is `reserved` before download, `active` after download, and `revoked`
when its source binding disappears or becomes invalid. The result reports
invalid profiles and unresolved scopes. Network handshake state is not inferred
or changed by reconciliation.

The authenticated deployment API retains its existing cell and scope file
formats and performs no reconciliation writes on GET. Its networks also come
from assigned profiles; a missing source network is explicitly inactive. The
versioned observability interface remains the five views below.

The live interface supports one current competition's 18-instance catalogue.
Reconciliation refuses another current catalogue rather than selecting one.
Completed and archived competitions leave live application, allocation and team
access publication; their scopes become inactive. Historical finding evidence
remains available through the findings view.

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
staff principal has competition-wide read scope. Those principals emit only
their global role even when they also belong to a team. Blue-team rows are
limited to their enabled, assigned application. Red-team rows require an active
target assignment, valid participant network and current engagement window.
Inactive accounts, withdrawn teams, revoked profiles and invalid target bindings
lose access at query time, without waiting for another reconciliation.

The findings view exports `finding_id`, `enterprise`, `application`, `namespace`,
`start_ts`, `end_ts`, `telemetry_event_ids` and `correlation_ids`, alongside the
version and update timestamp. The time window is inclusive UTC. Reference arrays
contain unique opaque identifiers and at least one cited event or correlation.
Malformed references and uncited prose reports stay in the platform and are not
exported through this view.

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
the role cannot select `auth_user`, `accounts_userprofile`,
`accounts_teammember`, `cyberrange_finding` or `cyberrange_wireguardprofile`.

## Validation and rollback

Hosted CI applies and reverses the corrective migration on disposable
PostgreSQL, then runs the full application suite on PostgreSQL and SQLite. The
native tests check all five column sets, positive and revoked access, unchanged
reruns, finding references and a reader restricted to the views. Local tests use
isolated files, captured email and a temporary SQLite database by default:

```bash
python manage.py test --settings=config.observability_test_settings
```

For native view tests, set `OBS_TEST_PG_SOCKET` to a disposable local PostgreSQL
socket directory, or `OBS_TEST_PG_HOST` to a loopback test service. These settings
use only the test database `obs_interface` and test role `obs_test`.

Published migrations remain intact; merge migrations join the account and
WireGuard runtime branches before the corrective contract migration. Prefer an
application rollback that keeps this compatible database schema. If the
corrective migration must be reversed, take a backup first: migrating to
`cyberrange 0003_merge_observability_runtime` restores the preceding view shapes
and reader grants but removes the new correlation-reference column. A rollback
to `cyberrange 0001` also removes existing WireGuard runtime fields and the
observability tables, so it is not a normal application rollback. None of these
commands deploys infrastructure services.

The AI telemetry destination, authentication and API remain AI-owned external
dependencies. This interface does not invent them.
