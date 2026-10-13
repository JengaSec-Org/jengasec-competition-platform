# Deploy agent

Turns a team's image reference into a running cell. Runs on **Server 3**, not
on the website.

## Why it works this way

Pulling, scanning and deploying needs Docker, Trivy and a cluster. The public
site has none of those, and Server 3 is never exposed to the internet
(ADR 0002). So the agent lives where the tools are and *polls* the website:

```
Server 3 (shared VM)                        the website
  agent.py  ──GET  /deployments/api/pending/──▶   what to pull
            ──POST /deployments/api/status/──▶    what happened
```

Outbound HTTPS only, in one direction. Nothing has to reach *into* Server 3,
and nothing has to reach into Render. That is why this keeps working whether
the site is on Render today or on the web VM tonight — only `--base` changes.

## Install

Python 3.9+ and nothing from pip. On the shared VM:

```bash
sudo apt install -y docker.io python3-yaml
curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sudo sh -s -- -b /usr/local/bin
# kubectl: already there if this box talks to k8s-blue
```

The agent degrades rather than refusing: no Trivy and the scan is recorded as
skipped; no kubectl and it stops after the scan and says so. Docker is the only
hard requirement.

## Configure

```bash
export JENGASEC_DEPLOY_TOKEN='…'           # same value as DJANGO_DEPLOY_TOKEN on the site
export JENGASEC_BASE='https://jengasec.com' # or http://192.168.40.11 once the site moves here
```

Generate the token once, and set the *same* string in both places:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put it in Render → Environment as `DJANGO_DEPLOY_TOKEN`. Until it is set, both
endpoints answer 404 on purpose, so a half-configured deploy exposes nothing.

## Run

```bash
python3 agent.py --once                  # one pass — put this on a cron
python3 agent.py --watch --interval 30   # stay up during the build window
python3 agent.py --once --no-deploy      # pull and scan only, before the cluster exists
python3 agent.py --once --only 41 -v     # one submission, loudly, for debugging
```

As a service:

```ini
# /etc/systemd/system/jengasec-deploy-agent.service
[Unit]
Description=JengaSec deploy agent
After=network-online.target docker.service

[Service]
Environment=JENGASEC_BASE=https://jengasec.com
EnvironmentFile=/etc/jengasec/deploy-agent.env   # holds JENGASEC_DEPLOY_TOKEN, chmod 600
ExecStart=/usr/bin/python3 /opt/jengasec/agent.py --watch --interval 30
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## What it does to each submission

| Stage | What has to be true | What the team is told when it is not |
|---|---|---|
| **Pulled** | the image is **private**, the token works, it is **linux/amd64**, it runs as a **non-root** user, it is under **2 GB** | the exact fix — new read-only token, `--platform linux/amd64`, add a `USER`, use a `-slim` base |
| **Scanned** | no **critical** vulnerability with a fix available | how many, and that a judge can accept a deliberate one |
| **Deployed** | the manifests apply into the team's namespace | the apply error |
| **Healthy** | the **readiness probe** on their own health path answers 200 | check the port and health path, plus the last 50 log lines |

Three details worth knowing:

- **The public-image check is a real check.** It tries `docker manifest inspect`
  with an *empty* docker config. If that succeeds the image needs no credential,
  so it is public and is rejected — a Red team could have studied it.
- **The team's health path becomes the readiness probe.** "Healthy" therefore
  means the check they were told about passed, not a separate request from
  somewhere else. `kubectl rollout status` is what decides it.
- **Each team's token lives in its own throwaway `DOCKER_CONFIG`**, deleted
  after the submission. One team's credential never sits in a shared
  `~/.docker/config.json` where the next pull could use it by accident. Tokens
  never appear in `argv` — they go in on stdin or in a 0600 file — because
  `argv` is visible to every user through `ps`.

## When something gets stuck

`/api/pending/` only returns submissions still at **Submitted**, so a crash
mid-way leaves a row at Pulled or Scanned that the agent will not pick up
again. To re-queue it: open the submission on the website
(**Operations → Applications → Record**), set it back to **Submitted**, save.
The agent takes it on the next pass.

## Range lifecycle (Server 3)

The lifecycle wrapper uses the same agent and authenticated website API:

```bash
python3 make-cells.py --process-pending
python3 make-cells.py --freeze
python3 make-cells.py --reset-day 1
python3 reconcile.py
```

`--process-pending` runs one existing pull/scan/deploy pass. For scheduled
processing, run it from cron; `agent.py --watch --interval 30` remains
available during the build window.

Configure `JENGASEC_COMPETITION_ID`, `JENGASEC_DEPLOY_TOKEN`,
`JENGASEC_REGISTRY`, and `JENGASEC_STATE_DIR` (default
`/var/lib/jengasec-range`). `--freeze --dry-run` and
`--reset-day N --dry-run` preview without making range changes. The freeze
copies approved images into the private registry by digest, stores the actual
digest on the website, and saves/checksums database dumps locally. The state
directory must be private, persistent, and backed up.

Each Application Blue team also needs a unique reserved MetalLB IPv4 address
recorded by an organiser in its welcome-pack record. The agent applies it to
the cell's `LoadBalancer` Service and reconciliation checks that MetalLB has
assigned it.

Day reset requires `JENGASEC_MAKE_SCOPE` to point to the existing
infrastructure repo's `make-scope.py`; it resets apps/databases, waits for
health, and then reapplies the competition's current scope rules fetched from
the website API. The script validates the scope in dry-run mode before reset
mutations. This repository does not create the Kubernetes cluster, Proxmox
VMs, or network policies. The range
exports the reserved cell IP in `cells.yaml`, `allocation.csv`, and
`scope-assignments.csv`; the infrastructure repository must consume those
fields and provision the IPs in its MetalLB pool before participant access.

The WireGuard endpoint runs on the separate network VM. Set
`JENGASEC_WG_DUMP_COMMAND` to an argv command that reads its `wg show all dump`
output (such as restricted SSH access); the default local command only works
if WireGuard is on the Server 3 host.

The platform/agent contract and API shapes are documented in
`../../docs/CONTRACT-deployments.md`.

## Testing it

```bash
python3 -m unittest -v          # the decision logic
```

End to end, before any team depends on it: push a tiny private image to your
own GHCR, submit it on the website as a test team, and run
`python3 agent.py --once -v`. It should walk to Healthy. Then try it with the
package set to Public and confirm it is rejected with the right message.

## Contract

`../../docs/CONTRACT-deployments.md` has the full request and response shapes.
If you rewrite this agent in something else, build to that document — the
website will not change underneath you.
