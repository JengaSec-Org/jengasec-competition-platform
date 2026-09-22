# Seeds — what populates the database, and how to apply each one

Two kinds. **Migrations** seed reference data automatically on every
`migrate` (Render runs this in `build.sh`); you never run them by hand.
**Commands** seed things that depend on your data (a competition id) or
that are only for development.

## 1. Seeded by `migrate` (automatic, idempotent)

| Migration | What it creates |
|---|---|
| `accounts/0001_create_roles` | auth Groups `blue_team`, `red_team`, `judge`, `partner` |
| `accounts/0004_create_team_roles` | derived Groups `captain`, `team_member` |
| `accounts/0011_backfill_verified_accounts` | marks every account that existed before email verification shipped as verified (one-off) |
| `competitions/0005_seed_attack_scenarios` | the red-team attack scenarios |
| `competitions/0010_retire_cloud_track` | retires the Cloud track (one-off) |
| `submissions/0002_seed_submission_types` | document types: Blue/Red Team Documentation & Report |
| `submissions/0005_seed_supporting_evidence` | the `Supporting Evidence` type |
| `submissions/0009_three_proposal_types` | `Application Blue Proposal`, `AI Defence Proposal`, `AI Red Proposal` |
| `submissions/0011_seed_penalty_schedule` | the Guide §10 penalty table (8 rows, strict/standard/relaxed %) — edit in Admin → Penalty schedule if the Guide differs |

To re-apply one (e.g. after editing its rows): `migrate <app> <previous>` then `migrate <app>` — or just edit the rows in the admin.

## 2. Commands you run

### Production (Render shell or locally against the production DB)

```bash
python manage.py ensure_superuser
```
First admin, from `DJANGO_SUPERUSER_USERNAME / _EMAIL / _PASSWORD`. Already in `build.sh`; no-op if the user exists.

```bash
python manage.py seed_application_briefs --competition <id> [--cap 10]
```
The nine JengaBank briefs (APP01–APP09) in both enterprises for that competition. Find the id in Admin → Competitions or the URL of the competition page. Re-running updates names/text and never duplicates.

```bash
python manage.py seed_policy                                  # v1.0, built-in text
python manage.py seed_policy --ver 1.1 --file conduct.md --summary "Clarified scope"
```
Creates **and publishes** a code-of-conduct version (Guide §6 step 4). Publishing makes it current and notifies every competitor to accept it; no team can submit a registration until all its members have. Re-running for the current version does nothing. The built-in text is a placeholder — put the real Code of Conduct in a file and use `--file`.

```bash
python manage.py check_email [--send-to you@example.com] [--drain]
```
Not a seed, but the thing to run when mail is not going out: shows the effective backend/host/user, tests the SMTP login, counts the queue, and with `--drain` sends whatever is waiting.

### Development only

```bash
python manage.py seed_dev_data            # create / update
python manage.py seed_dev_data --reset    # wipe the seeded rows first
```
Refuses unless `DEBUG=True`. Gives you one competition (`JengaSec 2026`) with deadlines, the briefs, placeholder rubrics, policy v1.0, and the cast of accounts (password printed at the end):

| user | what they are for |
|---|---|
| `admin` | organiser (Command Center, Users, Teams) |
| `judge`, `partner` | judge / partner dashboards |
| `amina` | Application Blue captain, Nyati Defenders — approved, `JS26-B-001`, cell `ENTA·APP03` |
| `brian` | Nyati member, security specialist |
| `wanjiru` | Application Red captain, Simba Cell — assembled; accept the conduct, then submit |
| `juma`, `zawadi` | Simba members (juma is deputy + specialist) |
| `kev` | no team; invitation from Simba waiting |
| `neema` | registered but **unverified** — shows the inert account |
| `fatuma` | staff-issued judge with no password yet (set-password link) |

## 3. Things that are *not* seeded (set them in the UI)

* The competition itself in production: Admin → Competitions → add, then **Timelines** for the deadlines and the penalty **enforcement level** (Guide §10).
* Rubrics and their criteria: Judging → Rubrics. The structure check maps a missing proposal section to the criterion whose name contains the keyword in `competitions/constants.py` (`REQUIRED_SECTIONS`, `TRACK_EXTRA_SECTIONS`) — name your criteria so those words appear (e.g. "Architecture quality", "Risk management").
* Recognised institutional email domains: `accounts/institutions.py` (code, not data).

## 4. Fresh production checklist

```bash
python manage.py migrate
python manage.py ensure_superuser
python manage.py seed_policy --file conduct.md
# then in the UI: create the competition, set deadlines + enforcement level, and:
python manage.py seed_application_briefs --competition <id>
python manage.py check_email --send-to you@example.com
```
