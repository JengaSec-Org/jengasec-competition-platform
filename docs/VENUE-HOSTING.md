# Running the site at the venue, with Render still out front

**What this gives you:** two copies of the same Django app serving the same
data — Render on the public internet, and the `web` VM on the venue network.
Participants in the auditorium reach the local one over the range network;
the public, partners and anyone off-site keep using jengasec.com.

**What it is not:** an offline mode. Both instances read and write the same
Neon database, so if campus internet dies, both stop. See *The honest risk*.

## Why this shape

The alternative — a second database on the VM, synced from Render — is what
the architecture document describes, and it is the right long-term answer. It
is not a thing to build the night before an event: two writable copies of the
same registrations is how teams get lost. One database, two app servers, is a
configuration change with no new code and nothing to go wrong at 08:30
tomorrow.

```
            ┌────────────── Neon (Postgres) ──────────────┐
            │                                             │
    Render (public)                             web VM 192.168.40.11
    jengasec.com                                venue network, VLANs 102–107
            │                                             │
    public, partners, off-site                 324 people in the auditorium
```

Backblaze stays shared too, so an ID document uploaded at the venue is visible
on Render immediately, and vice versa. Sessions live in the database, so a
person signed in on one instance is signed in on the other.

## On the web VM

```bash
git clone https://github.com/JengaSec-Org/jengasec-competition-platform.git /opt/jengasec
cd /opt/jengasec && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

`/etc/jengasec/web.env` — chmod 600, owned by the service user:

```ini
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=<the SAME value as Render>
JENGASEC_DB_URL=<the SAME Neon pooled URL as Render>

# Everything the venue will type or resolve to. Add the IP as well as the name:
# somebody always types the address.
DJANGO_ALLOWED_HOSTS=192.168.40.11,jengasec.range,jengasec.local,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://jengasec.range,http://192.168.40.11

# Links inside emails must still work for people who are NOT at the venue,
# so this stays the public address even here.
DJANGO_SITE_URL=https://www.jengasec.com

# Same object storage and mail as Render.
S3_BUCKET=jengasec-submissions
S3_ENDPOINT_URL=…
S3_REGION=…
S3_ACCESS_KEY_ID=…
S3_SECRET_ACCESS_KEY=…
BREVO_API_KEY=…

# Leave these UNSET here. One scheduler only, or every reminder is sent twice.
# DJANGO_CRON_TOKEN=
```

**`DJANGO_SECRET_KEY` must match Render exactly.** Sessions are signed with it,
so a different value means every signed-in person is logged out the moment they
land on the other instance — and the registry tokens already stored for image
submissions become unreadable, because `secret_box` derives its key from it
when `DJANGO_TOKEN_KEY` is unset.

Run it the same way Render does:

```bash
./venv/bin/python manage.py migrate --check     # should say nothing to do
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3
```

A systemd unit, `nginx` in front, and you are done.

## The HTTPS trap — read this one

With `DJANGO_DEBUG=False` the app sets `SESSION_COOKIE_SECURE` and
`CSRF_COOKIE_SECURE`. A browser never sends a secure cookie over plain HTTP, so
on `http://192.168.40.11` **nobody can log in** — the login form accepts the
password and bounces straight back to itself, with no error that mentions the
cause. This will eat an hour if you meet it cold at 08:30.

Two ways out, in order of preference:

**1. Give the venue instance TLS.** Issue a certificate from the range's
internal CA for `jengasec.range`, terminate it in nginx, and set:

```ini
DJANGO_CSRF_TRUSTED_ORIGINS=https://jengasec.range
```

nginx must pass the scheme through, or Django still thinks the request is
insecure:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Host $host;
```

Participants need the CA root, or they get a warning page — distribute it with
the WireGuard configs, or accept the warning on an isolated network.

**2. If TLS is not ready tonight**, and only then:

```ini
DJANGO_INSECURE_COOKIES=True
```

This turns the secure flag off so sign-in works over HTTP. It is safe *only*
because the venue network is isolated and participant VLANs run with client
isolation — on an open network it is a session-hijacking hole. `preflight`
treats it as a blocker on any instance whose `SITE_URL` is public, and as a
warning otherwise, so it cannot be left on by accident. Take it off the moment
the certificate exists.

## Pointing people at it

Nothing in the app hard-codes a hostname: every internal link is relative, and
only email uses `SITE_URL`. So a participant who opens `https://jengasec.range`
stays on the venue instance for the whole session, and one who follows a link
from an email lands on Render. Both work; both see the same data.

The deploy agent should point at whichever instance is nearer:

```bash
JENGASEC_BASE=https://jengasec.range   # on the venue network
```

## Checks before you trust it

On the VM:

```bash
./venv/bin/python manage.py preflight
```

No blockers, and the storage line must say object storage. Then, by hand:

1. Open `/healthz/` — plain 200, no redirect.
2. Sign in as a test competitor. **If the form bounces back, it is the cookie
   trap above.**
3. Open **My Team** and confirm the roster matches what Render shows.
4. Upload something small, then open it on Render — same file, same version.
5. Sign in as staff, open **Operations → Applications**, confirm the same rows
   as Render.
6. On Render, change a team's status; refresh the venue instance; it changed.

If 4 and 6 both work, the two instances really are one system.

## The honest risk

Both instances depend on Neon, which is on the internet. **If campus internet
fails, the venue instance fails too** — it is not a local fallback, and nobody
should believe it is one during the event.

What the venue instance genuinely buys you: low latency inside the auditorium,
a second front door if Render has an incident, and traffic from 324 people kept
off the uplink. What it does not buy: survival of an internet outage.

If you want that, the smallest honest version is a read-only standby: a local
Postgres replicating from Neon, and a documented decision that during an
outage the site is read-only until the link returns. That is a calm-week
project, not a tonight one.

## The AI evaluator (Ollama) — VM only

**Is a model necessary for scoring? No.** Every mark can be entered by a judge,
and nothing in the engine depends on a model being present. What the model does
is read a proposal against the rubric and *suggest* a score per section, with
the evidence it based it on, so a judge reviews and corrects rather than reading
eighty documents cold. The Entry Guide says proposals are judged "assisted by
AI", and `services/ai_service.py` says it plainly: AI output is decision support.
**An approved evaluation becomes a mark; a model's number never does on its own.**

It runs on the VM, not on Render — Render has no GPU, no spare memory and a
request timeout a 20-page proposal would blow straight through.

### Install and pull the model

```bash
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable --now ollama
ollama pull qwen3.5:4b          # the default; ~2.5 GB
ollama list                     # confirm it is there
```

Then in `/etc/jengasec/web.env`:

```ini
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.5:4b
```

Both already have those defaults in `config/settings.py`, so the only thing
that actually has to happen is **the pull** — without it every run fails with
"model not found".

**Sizing.** A 4B model at 4-bit needs roughly 3–4 GB of RAM and will run on CPU.
The architecture gives the web VM 4 vCPU and 6 GB, which is enough but not
fast: budget a couple of minutes per proposal on CPU, so eighty proposals is an
hour or two, not a coffee break. Start it and go and do something else. If the
VM has more memory to spare, `qwen3.5:7b` or `llama3.1:8b` read long documents
noticeably better — set `OLLAMA_MODEL` and pull that instead. Nothing in the
code cares which model it is.

### Running it

```bash
python manage.py seed_proposal_rubrics --confirm   # once — the rubric
python manage.py parse_submissions --failed        # text must exist first
python manage.py score_proposals --dry-run
python manage.py score_proposals --confirm
```

`score_proposals` checks Ollama is reachable **before** it starts, so a missing
model fails in one line rather than once per document. It creates one
evaluation per proposal with per-criterion scores, evidence and reasoning, and
**awards nothing**. Judges then open the review queue, correct what is wrong and
approve — and approval is what records the team's Entry mark.

Re-running is safe: an evaluation a judge has already approved is never
re-scored. `--redo` re-runs the ones only the model has touched, `--team` does
one team, `--limit` does a handful first so you can read them before committing
to the lot.

### Before you trust it

Score three proposals you have already read yourself and compare. The model has
never been calibrated against this rubric, and the first thing to find out is
whether it is systematically generous or harsh — which is exactly why approval
is a human step.

## One scheduler, one agent

Two instances running the reminder jobs means two emails to every competitor.
Leave `DJANGO_CRON_TOKEN` set on **Render only**. The same goes for
`freeze_applications`: run it in one place, once.
