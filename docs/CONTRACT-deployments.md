# Contract: application image submissions and Server 3 range lifecycle

**Owner:** competition platform (this repository).
**Audience:** the website and the Server 3 range operator.

Blue teams submit an image reference and a read-only token on their team page,
exactly as the Image Submission Guide and the 11 October briefing told them.
The platform records submissions and serves authenticated APIs. The Server 3
agent pulls, scans and tests pending submissions; the Server 3 lifecycle
commands freeze approved images and seed databases, restore them for each
round, and report runtime observations.

## What the platform guarantees

- One row per attempt, never overwritten. A team may resubmit as often as it
  likes until the build deadline, which is a hard close.
- The pull token is encrypted at rest (Fernet) and appears in no page, no CSV
  export and no log. The only ways out are the authenticated pending/freeze
  candidate APIs and an organiser clicking "Show token", which is audited.
- `Healthy` is the only status that counts as passing. At the freeze the last
  passing attempt per team is pinned.
- Cell namespaces use the infrastructure contract
  `jengasec-ent-{enterprise}-{cell_id}`, for example
  `jengasec-ent-a-app01`. An assigned `Team.namespace` must match that value.
- The infrastructure chart provisions cell namespaces, database resources and
  credentials, quotas, network policies, ingress, and workload identity. The
  range agent only applies the app Deployment, app Service, and registry pull
  Secret inside an existing namespace; it does not create or modify the cell's
  other controls.
- Database-enabled submissions consume the chart-managed `db-credentials`
  Secret. The agent maps its `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`
  entries to the standard `PGHOST`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`
  environment variables, with `PGPORT=5432`.

## Authentication

All deployment endpoints take a shared secret in a header:

```
X-Deploy-Token: <DJANGO_DEPLOY_TOKEN>
```

- The token is unset by default, and these endpoints then answer **404** — a
  deployment that forgot the variable exposes nothing.
- A wrong or missing token gets **403**. Comparison is constant-time.
- Same pattern as the scheduled notification runner at `/tasks/run/`.

## `GET /deployments/api/pending/`

Everything waiting on its first check, oldest first, up to 100.

```json
{
  "pending": [
    {
      "id": 41,
      "competition": 6,
      "enterprise": "a",
      "cell": "APP01",
      "namespace": "jengasec-ent-a-app01",
      "service_ip": "10.108.0.11",
      "team": "JS26-B-014",
      "attempt": 3,
      "image_reference": "ghcr.io/amina-dev/internet-banking-portal:v3",
      "registry_username": "amina-dev",
      "pull_token": "ghp_...",
      "port": 8080,
      "health_path": "/healthz",
      "needs_database": true,
      "env_vars": {"APP_MODE": "production"},
      "extra_images": ["ghcr.io/amina-dev/internet-banking-worker:v3"]
    }
  ],
  "blocked": [
    {"id": 44, "team": "JS26-B-015", "error": "Application cell ENTA · APP02 needs a reserved service IP."}
  ]
}
```

`blocked` lists submissions whose team cannot be deployed yet: no enterprise, cell or reserved
service IP. They stay at **Submitted** and appear in `pending` once an organiser fixes the team.
Before this field existed, one such team made the whole call answer `409`, which stopped every
other team's deployment.

Notes:

- **This response carries a credential.** It is `Cache-Control: no-store`, it is
  audited every time it returns rows, and it must only ever be called over
  HTTPS on the functions VLAN. Do not log the body.
- `env_vars` is non-secret settings only; the form refuses anything whose key
  looks like a password, token or identifier. Real secrets are mounted by the
  range, never typed into the platform.
- `extra_images` is at most two, and uses the same token.
- `cell` is the platform's cell code (`APP01`), with `enterprise` separate
  (`a`/`b`). `namespace` follows the shared infrastructure contract, for
  example `jengasec-ent-a-app01`.
- `service_ip` is the reserved MetalLB IPv4 address assigned by an organiser
  in the team's welcome-pack record. It is exported to cells YAML, allocation
  CSV and scope-assignment CSV; it must be assigned before a cell is deployed.

## `POST /deployments/api/status/`

One check, done or failed.

```json
{
  "id": 41,
  "status": "scanned",
  "detail": "3 high, 0 critical",
  "scan_report": {"critical": 0, "high": 3},
  "logs_excerpt": "listening on 8080"
}
```

`status` is one of:

| status | meaning |
|---|---|
| `pulled` | logged in with the team's token and copied the image |
| `scanned` | scanned; `scan_report` holds the result |
| `deployed` | started in the team's namespace |
| `healthy` | the health path returned 200. **This is a pass** |
| `failed` | it stopped here; put the reason in `detail` |
| `rejected` | refused on a rule (public image, root user, wrong architecture) |

- `detail` is shown to the team verbatim, so write it as something they can act
  on — "image built for ARM, rebuild with --platform linux/amd64", not
  "exec format error".
- `scan_report` and `logs_excerpt` are optional and both surface on the team's
  page; the Guide promises teams they can see why it failed.
- An unknown status is **400** and nothing is stored. An unknown `id` is 404.
- Posting the same status twice is harmless.

Response:

```json
{"id": 41, "status": "healthy", "is_passing": true}
```

## Server 3 lifecycle APIs

All range endpoints below use `X-Deploy-Token` and return 404 if
`DJANGO_DEPLOY_TOKEN` is not configured. The shared secret must be set on the
website and on Server 3 as `JENGASEC_DEPLOY_TOKEN`; use HTTPS between them.

### `GET /deployments/api/freeze-candidates/?competition=<id>`

Returns the latest passing, not-yet-frozen submission for each eligible
Application Blue team, including the source registry credentials needed for
the copy. The response is `Cache-Control: no-store` and audited. Teams with no
passing image appear in `missing`; the Server 3 freeze command aborts before
copying anything when that list is non-empty.

### `POST /deployments/api/freeze/`

Records the immutable digest after Server 3 copies an image into the JengaSec
registry. The request is `{"id": <submission id>, "digest":
"<registry>/<image>@sha256:<64 hex>", "extra_digests": []}`. It accepts only
the team's current last passing submission and an immutable digest per primary
and extra image. Repeating the same request is safe.

### `GET|POST /deployments/api/range-state/`

GET returns frozen app definitions and eligible WireGuard peer addresses; an
optional `?competition=<id>` limits cells. Without it, all competitions are
returned so the reconciliation command can run as `python reconcile.py`.
POST accepts `cells` and `peers` observations and persists health, observed
digests, and last-handshake state. It does not return WireGuard private keys.
App definitions include the reserved `service_ip`; reconciliation reports a
cell healthy only when its LoadBalancer Service requests that address and
MetalLB reports it assigned.

### `GET /deployments/api/range-exports/?competition=<id>`

Returns the competition's current `cells.yaml` and `scope-assignments.csv`
contents as `cells_yaml` and `scope_csv`. The endpoint uses the same
`X-Deploy-Token` authentication and `Cache-Control: no-store` policy as the
other range APIs. Server 3 consumes these exports immediately before applying
scope, so it does not depend on manually refreshed files in the infrastructure
checkout. `round_active` reflects each assignment's current `is_active` flag;
the platform does not currently store a separate scope schedule by reset day.
Organisers must update assignment activity before running a reset when the
allowed teams are meant to change.

## Freeze and reset

These commands run on Server 3, not on the website:

```bash
python tools/deploy-agent/make-cells.py --freeze
python tools/deploy-agent/make-cells.py --reset-day 1
```

Set `JENGASEC_COMPETITION_ID`, `JENGASEC_REGISTRY`,
`JENGASEC_DEPLOY_TOKEN`, and `JENGASEC_STATE_DIR` (default
`/var/lib/jengasec-range`). Use `--dry-run` to preview. Freeze copies the last
passing primary and extra images to the private registry, stores their
immutable digests in the website, and writes PostgreSQL seed dumps with
checksums into the private state directory. Back up that directory securely.

Reset verifies the frozen digests and seed checksums, reapplies the existing
workloads, restores the database dump, waits for application health, and then
executes the infrastructure repository's `make-scope.py`. Set
`JENGASEC_MAKE_SCOPE` to that executable path. The scope script retrieves the
current competition exports from `api/range-exports/` using the deployment
token. It validates the scope plan in dry-run mode before changing workloads,
then applies it after applications are healthy.

## Reconciliation

Run a single check or a service loop:

```bash
python tools/deploy-agent/reconcile.py
python tools/deploy-agent/reconcile.py --watch --interval 60
```

The WireGuard interface lives on the separate networking VM, not the shared
Server 3 VM. Configure `JENGASEC_WG_DUMP_COMMAND` as a command argv that can
read the remote `wg show all dump` output (for example, an SSH command with
restricted key/host access). Do not pass shell syntax: the command is
tokenized and executed without a shell.

## Known infrastructure prerequisites

- The Kubernetes cluster, private registry, credentials, and scope script must
  already exist and be reachable from Server 3. This automation does not
  create Proxmox VMs or install the cluster.
- PostgreSQL seed files contain competition data. Keep
  `/var/lib/jengasec-range` private, persistent, access-controlled, and backed
  up; the database dumps are intentionally not stored in the website.
- The app Service now requests its reserved MetalLB address. Those addresses
  are stored per team and emitted in all range exports. The infrastructure
  repo must consume the added `service_ip` / `target_service_ip` fields and
  provision those addresses in its MetalLB pool. Network policies still need
  to be connected to the infrastructure repo before cells are reachable from
  the participant attack network.

## Still open

- Red-team toolbox images are **not** offered. `k8s-red` is held in reserve and
  was deliberately not promised to teams before the event, so the platform does
  not invite a submission it may not be able to honour.

## Verified artifacts for scoring

The authenticated status receiver accepts an optional `resolved_digest` in
`sha256:<64 lowercase hexadecimal digits>` form. The range resolves the pulled
registry manifest and deploys that reference. A healthy receipt records when the
range verified the deployed artifact. An image tag alone is insufficient.

`freeze_applications` reports candidates; it does not freeze their content.
After the range copies the primary and extra images into its registry,
`POST /deployments/api/freeze/` records their full `repository@sha256` references.
The primary hash also becomes `frozen_verified_digest`. Repeating the same
receipt preserves the freeze. A different pinned primary or extra image is
rejected. A legacy tag from the old selection command needs a real registry
copy before it can supply this proof; the new proof is not backdated.

A registry copy does not confirm a running workload and does not refresh
`deployed_digest_verified_at`. The range must separately observe the deployed
pods. Scoring requires that observation to be at most 120 seconds old.

`GET /deployments/api/active/` returns frozen submission IDs, enterprise, cell,
assigned namespace, `frozen_verified_digest`, `frozen_digest`, and
`frozen_extra_images`. It returns no registry credentials.
Use `after` and `next_cursor` to page through up to 100 entries per response.

`POST /deployments/api/artifact/` accepts `{id, resolved_digest, extra_digests}` with the same
`X-Deploy-Token`. The range must inspect the assigned deployment before sending
this receipt. It refreshes artifact identity without changing application health.
A different digest is recorded as a mismatch and cannot replace the frozen
digest. A missing confirmation expires scoring eligibility after 120 seconds.
`extra_digests` contains observed bare hashes in frozen extra-image order. It
can be omitted for an application with no extra images. Every frozen extra
image must match before this endpoint or a healthy status receipt can renew
the artifact confirmation. Freezing an extra-image set clears any earlier
primary-only runtime confirmation.

The normal range reconciler also refreshes this observation through
`POST /deployments/api/range-state/`. It reports the actual app-container image
digest only when every observed app pod identifies the same content and all
extra-container references and actual hashes match the frozen set. Missing,
mixed, or mismatching extra-image proof supplies no attestation. Its POST rows
include `extra_digests` as bare hashes in the same order. A complete artifact
observation is still reported when readiness or the service health check fails.

Run the deployment agent with `--verify-artifacts` and a polling interval of
30 seconds after these endpoints are deployed. App health failures remain
measurable availability failures while the frozen workload identity is known.
