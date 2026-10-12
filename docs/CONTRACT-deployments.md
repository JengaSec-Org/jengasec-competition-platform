# Contract: application image submissions

**Owner:** competition platform (this repository).
**Audience:** whoever builds the registry puller, the Trivy scan and the test
deploy on the shared VM (APP in *Who Does What*).

Blue teams submit an image reference and a read-only token on their team page,
exactly as the Image Submission Guide and the 11 October briefing told them.
The platform **records** those submissions and shows teams the four checks. It
does not pull, scan or deploy anything — that is yours, and these two endpoints
are how you drive it.

## What the platform guarantees

- One row per attempt, never overwritten. A team may resubmit as often as it
  likes until the build deadline, which is a hard close.
- The pull token is encrypted at rest (Fernet) and appears in no page, no CSV
  export and no log. The only ways out are `GET /api/pending/` below and an
  organiser clicking "Show token", which is audited against their account.
- `Healthy` is the only status that counts as passing. At the freeze the last
  passing attempt per team is pinned.

## Authentication

Both endpoints take a shared secret in a header:

```
X-Deploy-Token: <DJANGO_DEPLOY_TOKEN>
```

- The token is unset by default, and both endpoints then answer **404** — a
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
  ]
}
```

Notes:

- **This response carries a credential.** It is `Cache-Control: no-store`, it is
  audited every time it returns rows, and it must only ever be called over
  HTTPS on the functions VLAN. Do not log the body.
- `env_vars` is non-secret settings only; the form refuses anything whose key
  looks like a password, token or identifier. Real secrets are mounted by the
  range, never typed into the platform.
- `extra_images` is at most two, and uses the same token.
- `cell` is the platform's cell code (`APP01`), with `enterprise` separate
  (`a`/`b`). If you want the range's `enta-app01` form, join them your side —
  and see the note on identity below.

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

## The freeze

At the build deadline, run on the platform:

```bash
python manage.py freeze_applications --dry-run
python manage.py freeze_applications --confirm
```

It pins each team's last passing attempt and **lists every team with no passing
image** — those teams have no application in the competition, so that list is
the one to act on. It is idempotent, and exits non-zero while any team is
missing.

`frozen_digest` currently records the image reference that was pinned, because
the reference is what the platform can honestly assert. If you resolve the real
content digest when you copy into JengaSec's registry, post it back and we will
store that instead — say the word and I will add a field to `/api/status/`.

## Still open

- **Team identity.** You key on cell codes (`enta-app01`) and team codes
  (`AI-01`); the platform keys on `enterprise` + `cell_id` + `team_identifier`.
  The payloads above give you all three so nothing is lost, but we should agree
  one key before scoring integrates, or a result will land on the wrong team.
- Red-team toolbox images are **not** offered. `k8s-red` is held in reserve and
  was deliberately not promised to teams before the event, so the platform does
  not invite a submission it may not be able to honour.
