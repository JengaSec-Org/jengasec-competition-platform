# Handoff: sending notifications (Module 12)

**For:** whoever builds accounts, registration and team invitations.
**From:** the notifications side.

Every notification the platform sends is one function call in
`services/notification_catalogue.py`. You never build a `Notification` row,
never touch email, and never have to remember who should hear about what.

## How it works

```
your flow  ->  notification_catalogue.<event>()  ->  in-app row (instant)
                                                 ->  email queue (drained by cron)
```

Creating a notification never touches SMTP. `send_notification_emails`
delivers queued mail every five minutes, so a slow mail server can never
fail your request.

Two things are true of every function here:

* **They are idempotent.** Each supplies a `dedupe_key`, and a partial
  unique index refuses the second copy. Call one twice and the second call
  is a no-op — that is the intended way to handle retries.
* **They are safe inside `transaction.atomic`.** Every signal-driven caller
  defers to `transaction.on_commit`. If you call the catalogue directly
  inside a transaction, do the same, or a rolled-back registration will
  have told the team it succeeded.

## Already wired — do nothing

These fire from `notifications/signals.py` or a cron command when the
underlying state changes:

| Event | Trigger |
|---|---|
| Registration submitted | `Team` created |
| Approved and assigned | `Team.status` becomes `approved` |
| Rejected | `Team.status` becomes `rejected` (reads `Team.rejection_reason`) |
| Registration closing | `send_registration_reminders`, at 7d / 2d / 12h |
| Member removed or withdrew | `TeamMember` deleted |
| Outstanding items | `send_outstanding_digest`, daily |
| Submission uploaded / evaluated / results published / appeal filed / appeal resolved | submissions and judging signals |

Two notes on the wired ones:

**Registration submitted** fires when the `Team` row is created, which for
a flow that adds members afterwards means the receipt reaches only the
captain. If your flow has a distinct "submit registration" moment, call
`registration_submitted(team)` explicitly there instead — the dedupe key is
the same, so whichever happens first wins and the other is a no-op.

**Rejected** sends `Team.rejection_reason` verbatim to the captain. Make
your rejection form require it. "Rejected" with an empty reason produces a
notification that says nothing useful, and the captain has no way to
correct whatever was wrong.

## Yours to call

The event is real but the flow that produces it does not exist yet. Each
takes plain arguments — no model of ours is assumed.

```python
from services import notification_catalogue as cat
```

### Account created
```python
cat.account_created(user, verification_url, expires_hours=24)
```
Call straight after creating the user, before any other mail: until the
address is verified it is unproven. Deliberately **not** deduplicated — a
resent verification link is a new message, not a duplicate.

### Email verified
```python
cat.email_verified(user)
```

### Invitation sent
```python
cat.invitation_sent(email, team, captain, accept_url, expires_days=7,
                    invitee_user=None)
```
The invitee usually has no account. With `invitee_user=None` this returns a
`{"to", "subject", "body"}` dict for you to send directly, because there is
no user to attach an in-app row to. Pass `invitee_user` when the address
already belongs to an account and they get both.

### Invitation accepted
```python
cat.invitation_accepted(team, member, outstanding=[...])
```
Goes to the captain. Pass `outstanding` — reuse
`send_outstanding_digest.outstanding_for_member(member)` — so the captain
sees what that person still owes rather than just that they joined.

### Approved but waitlisted
```python
cat.registration_waitlisted(team, position, note="")
```
`position` is 1-based. There is no waitlist model yet; when you add one,
this is the only call needed.

### Policy version updated
```python
cat.policy_updated(users, version, summary, accept_url)
```
One `bulk_create` for everyone. The dedupe key includes `version`, so each
new version notifies exactly once even if you call it repeatedly.

## Extending the outstanding-items rule

`send_outstanding_digest.outstanding_for_member(member)` is the single
definition of "outstanding", and it currently covers only what the schema
can answer today: no linked account, no email, no name, no role, no
institution.

As your flows land, add to that one function — verified email, accepted
policy version, signed waiver, accepted invitation. Everything that reads
it improves at once: the daily digest, the captain's view on invitation
accepted, and the registration-closing reminder.

Keep entries phrased as instructions to the person receiving them
("Create a platform account using this email address"), not as field
names. They are sent verbatim.

## Adding a new notification type

1. Add the choice to `Notification.Type` and make a migration.
2. Add a function to `notification_catalogue.py`, with a `dedupe_key`.
3. Add `templates/notifications/email/<type>.txt`. If you skip it,
   `default.txt` renders and nothing breaks.

## Not done, and deliberately not ours

Creating accounts, verifying addresses, invitations, waitlists and policy
records. Notifications announce state changes; they do not own the state.
