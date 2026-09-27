# Go-live checklist — registration phase

Run `python manage.py preflight` on the host at any point; it checks most
of this automatically and exits non-zero while anything is a blocker.

## Blockers — registration does not work without these

| What | How |
|---|---|
| Real database | `JENGASEC_DB_URL` (Neon). SQLite on Render is wiped every deploy — the whole field's registrations with it. |
| `DJANGO_DEBUG=False` | Leaving it on exposes tracebacks with settings in them. |
| `DJANGO_SECRET_KEY` | Render's blueprint generates one; an existing service needs it set. |
| `DJANGO_ALLOWED_HOSTS` | Must list `jengasec.com,www.jengasec.com`, or Django answers 400. |
| `DJANGO_SITE_URL` | `https://www.jengasec.com`. Every verification and invitation link is built from it — wrong value, nobody can verify. |
| Email that leaves the box | `BREVO_API_KEY`. Render blocks outbound SMTP, so Gmail app passwords cannot work there. |
| Object storage | `S3_BUCKET` + `S3_ENDPOINT_URL` + `S3_REGION` + `S3_ACCESS_KEY_ID` + `S3_SECRET_ACCESS_KEY`. Identity documents and proposals live on disk otherwise, and the disk is wiped on deploy. |
| An open competition | Status **Open**, phase **Registration**, with a registration deadline set. |
| Application briefs | `seed_application_briefs --competition <id>` — nine of them; blue teams have nothing to propose against otherwise. |
| A published code of conduct | `seed_policy --file conduct.md`. Every registration is blocked on accepting it, so without one nobody can submit. |
| An organiser account | `ensure_superuser` (already in `build.sh`). |

## Do before opening the doors

* Delete the development seed accounts if they ever reached this database:
  `User.objects.filter(email__endswith="@jengasec.test").delete()`
* Set the **penalty enforcement level** on the competition (Competitions →
  Timelines). Teams are told which level applies before the window opens.
* Send yourself a test registration end to end: register → verify → create a
  team → invite someone → accept → complete both profiles → accept the
  conduct → submit → approve it as an organiser.
* `python manage.py check_email --send-to you@example.com --drain`

## Scheduled jobs

Delivery of queued email no longer needs a scheduler — it goes out when the
request commits. These still want one (Render free has no cron; a free
[cron-job.org](https://cron-job.org) hit on a small management endpoint, or
a paid Render cron, both work):

| Command | When |
|---|---|
| `send_registration_reminders` | hourly — 7 d / 2 d / 12 h warnings to incomplete teams |
| `send_outstanding_digest` | daily — per-member checklist nudges |
| `send_notification_emails` | every few minutes — retries anything a send failed on |

Without them registration still works; teams just do not get chased.

## Known gaps (not blockers for registration)

* **Judging, scoring and results** are built but unexercised — they belong to
  the phases after selection, and the rubric criteria still need writing
  against the real Guide.
* **Analytics** covers the registration phase only (turnout, funnel,
  blockers, brief demand). Scoring analytics come with judging.
* **Exports** are CSV. The scored-result PDFs (`services/report_service.py`)
  are still stubs and are a judging-phase concern.
* The **page bands, required sections and penalty table** in
  `competitions/constants.py` were written from the roadmap, not the Guide's
  own text. Check them before proposals open.
