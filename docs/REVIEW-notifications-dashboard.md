# Review: the notifications work from `feature/dashboard`

**For:** Irishura, who wrote `feature/dashboard` (`0f557af`).
**From:** the integration side, while resolving that branch's merge with `main`.

Your commit is now merged on `backup/dashboard`, but not in the shape you left
it. This is the honest account of what changed and why, so nothing about the
result is a surprise.

## What was kept

- **The service layer.** `notify_user` / `notify_admins` / `notify_judges` in
  `services/notification_service.py` was the right instinct: views should not
  build `Notification` rows by hand. That shape survives and is now the only
  supported way to create one.
- **The `link` field.** `main`'s notification model had no way to say *where a
  notification takes you*, and yours did. It is now a real field on the merged
  model (migration `0002_notification_link`).

## What changed, and why

### 1. The model is `main`'s, not yours

Both branches wrote a `notifications` app independently. That is the entire
merge conflict — three files, including two different `0001_initial.py`
migrations for the same table.

`main`'s version won because it is the one built against *JengaSec Database
Design V1*: seven notification types including appeals and deadlines, a
`sent_at` column for the email path, an `ix_notification_unread` index that the
badge query needs, and a `mark_read()` method. Yours had four types and no
index.

Concretely, the field names moved:

| yours | now |
|---|---|
| `recipient` | `user` |
| `notification_type` | `type` |
| `title` | `subject` |
| `message` | `body` |
| `link` | `link` (kept) |

This is not a judgement about which model is nicer. It is that `main`'s was
already migrated, already referenced by `notifications/forms.py`, and matches
the schema document the rest of the team is building against.

### 2. `.vem`

`judging/views.py` line 4 was a bare line reading `.vem` — a stray keystroke.
Python cannot parse it, so the module never imported and `manage.py check`
failed on the branch.

It is worth sitting with what that implies: the branch could not have been run
even once before it was pushed. Running `python manage.py check` before you
commit costs two seconds and would have caught this, the missing templates, and
the empty URL patterns below.

### 3. `Submission.team` is a `Team`, not a `User`

This one is subtle and cost you three separate bugs:

```python
submission.team = request.user                      # Team field, User value
Submission.objects.filter(team=request.user)        # same mismatch
notify_user(submission.team, ...)                   # Team passed where a User goes
```

`accounts.Team` is a real model with a `captain` and `TeamMember` rows. A user
is not a team; they *belong* to one. `feature/test-branch` already has the
correct lookup, and it is worth copying rather than rewriting:

```python
qs.filter(
    Q(team__captain=user)
    | Q(team__members__user=user)
    | Q(team__members__email__iexact=user.email)
).distinct()
```

### 4. `upload_submission` never uploaded anything

The view called `SubmissionForm(request.POST, request.FILES)`, but that form's
fields are `["team", "competition", "submission_type"]` — no file field. No
`SubmissionFile` row was created, so no version, filename, size, mime type or
checksum either.

`main` already ships `SubmissionFileForm`, which validates the extension and
the 50 MB cap and has a `populate_metadata()` helper for exactly this. It was
sitting unused.

`Submission` is a *thread*; each upload is a new `SubmissionFile` version on it.
The checksum is not decoration — it is the dispute-proof if a team ever claims
the file they submitted is not the file that was marked.

### 5. The views were unreachable

`submissions/urls.py` and `judging/urls.py` were both still `urlpatterns = []`,
so nothing routed to the new views, and `redirect("submissions:submission_list")`
and `redirect("judging:review_list")` would have raised `NoReverseMatch` if
anything had. Four referenced templates did not exist either.

### 6. `approve_score` approved nothing

```python
def approve_score(request, submission_id):
    if request.method == "POST":
        notify_user(...)          # <- the entire body
        messages.success(...)
```

No score written, no status transition, no record of who approved what or when.
On a judging platform this is the one action that must leave an audit trail —
`services/audit_service.record(...)` and `AuditLog.Action.SCORE_OVERRIDDEN`
exist for it.

### 7. `mark_read` mutated on GET, and followed an unvalidated link

```python
notification.save(update_fields=["is_read"])
return redirect(notification.link or "notifications:list")
```

Two problems. A GET request must never change state — a prefetching browser, a
crawler, or an `<img src>` somewhere else could fire it. And `link` is stored
text: once an organiser can compose a notification, that `redirect()` will
follow any URL it contains, including off-site.

It is now `@require_POST`, and the link is checked with
`url_has_allowed_host_and_scheme` before being followed.

### 8. Removed

`submissions/views.py` and `judging/views.py` were reverted to `main`'s
versions. Not as a verdict on the code — `feature/submissions` landed a full
submission wizard, file versioning and dashboards while this branch was open,
and it supersedes both files entirely.

## The process bit

Every problem above except `.vem` traces to one root cause: the branch was cut
from `1c53d8c` and never pulled `main`. Ten weeks of schema work landed on
`main` in the meantime, including a notifications app.

Two habits prevent all of it:

```bash
git fetch origin && git merge origin/main   # at the start, and weekly
python manage.py check                      # before every commit
```

Nothing here was wasted — the service layer and the `link` field are in the
merged code. The duplication was avoidable, and that is the only thing worth
changing next time.
