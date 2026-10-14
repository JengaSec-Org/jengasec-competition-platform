# Going live — the event-day runbook

Everything in order, with the command for each step. Run the whole thing on
**one** instance: Render and the VM share the Neon database, so a command run
in either place changes both. Pick Render for anything that sends email.

---

## 1. Generate the four secrets

Run this four times and keep the output somewhere you can paste from:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Variable | Where it goes | What it guards |
|---|---|---|
| `DJANGO_DEPLOY_TOKEN` | Render **and** VM | `/deployments/api/*` — the deploy agent |
| `DJANGO_FEED_TOKEN` | Render **and** VM | `/api/feed` — observability pushing marks |
| `DJANGO_CRON_TOKEN` | **Render only** | `/tasks/run/` — reminder jobs |
| `DJANGO_TOKEN_KEY` | Render **and** VM | encrypts stored registry tokens |

Use the **same value in both places** for the first two and the last, so the
agent and observability can talk to whichever instance is nearer.

`DJANGO_CRON_TOKEN` goes on **Render only**. Two schedulers means every
competitor gets every reminder twice.

`DJANGO_TOKEN_KEY` wants a Fernet key, not a URL-safe token:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Leave it unset and the key is derived from `DJANGO_SECRET_KEY` — which works,
but means the two instances must share `DJANGO_SECRET_KEY` exactly or stored
registry tokens become unreadable on one of them.

Render: **Dashboard → the service → Environment → Add**. Each one triggers a
redeploy. VM: `/etc/jengasec/web.env`, `chmod 600`, then restart the service.

---

## 2. Check the deployment is sane

```bash
python manage.py preflight
```

No blockers. The storage line must say object storage, and `DEBUG` must be off.
On the VM, if the login form accepts a password and bounces straight back, it
is the secure-cookie trap — see `VENUE-HOSTING.md`.

---

## 3. Publish the marking scheme

```bash
python manage.py seed_proposal_rubrics --dry-run
python manage.py seed_proposal_rubrics --confirm
```

Creates both proposal rubrics from the Entry Guide. Judges then see the whole
scheme at **`/scoring/scheme/`** — the three stages, all four sides' criteria,
both rubrics and the penalty table.

---

## 4. Carry the qualifier into Entry marks

```bash
python manage.py seed_qualifier_scores --dry-run
python manage.py seed_qualifier_scores --confirm
```

360 points earns the full 30; above that is bonus. Change the mark with
`--max 400`, or drop the bonus entirely with `--no-bonus`.

**Read the dry run before confirming.** Any scoreboard row that matches no
registered team is listed, and so is any registered Red team with no qualifier
score. Neither is scored, and neither fixes itself.

---

## 5. Assign Red teams to applications

```bash
python manage.py assign_targets --dry-run
python manage.py assign_targets --confirm
```

Spreads whatever turned up as evenly as it can — two or three Red teams per
application rather than three, three, three, one — and deals the strongest
qualifiers onto different applications. `--per 4` raises the cap if turnout
needs it.

Nothing is visible to Red teams yet. When the window opens:

```bash
python manage.py assign_targets --confirm --activate
```

That is the switch that lets Red teams see their target and file findings.
Endpoints and access notes go on each assignment in Django admin, or the range
pushes them.

---

## 6. Open the phase

**Competitions → the competition → Run the Competition → Phase: Live → Set
Phase.** This is what opens Target Access, Live Status and the findings form.
There is no command and deliberately no automatic advance.

---

## 7. Put the scoreboard up

**`/scoring/live/`** — standing on top, marks as they land underneath,
refreshing itself every 30 seconds. Leave it on the projector.

Participants see it **5 minutes behind**; staff and judges see it live. The page
says which it is showing, so nobody has to guess.

---

## 8. During the days

| When | Command |
|---|---|
| A judge marks a finding valid | nothing — the Red team is rescored automatically |
| Documents arrive | `python manage.py parse_submissions --failed` |
| Proposals to pre-score (VM) | `python manage.py score_proposals --confirm` |
| Reminders | the Notification Console, or let the hourly job run |
| Something looks wrong in a total | `python -c "..."` → `scoring_service.recompute_all(competition)` |

Marks are entered at **`/scoring/team/<id>/mark/`**. Application Red's main
competition marks are computed from findings and cannot be typed — change the
finding instead.

---

## 9. At the end

```bash
python manage.py freeze_applications --confirm     # if not already done
```

Then **Close Judging** and **Publish Results** on the same panel. Publishing
releases feedback and starts the appeal window.

---

## The order that matters

Rubrics before proposals are scored. Qualifier before `assign_targets`, so the
strongest teams are dealt apart. Assignments before the phase goes Live, or Red
teams land on a findings form with nothing in scope.

Everything else can be run in any order, and everything here is safe to run
twice.
