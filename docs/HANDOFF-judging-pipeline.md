# Handoff: connecting submissions to judging (Modules 6–9)

**For:** whoever owns rubrics, AI evaluation, human judging and score aggregation.
**From:** the submissions / administration side.

## The gap

Nothing in the codebase creates an `Evaluation`. Submissions arrive, are parsed,
and stop there. Consequences you will see today:

- the Command Center's **Pending Reviews** tile is structurally always 0;
- a judge's **insights dashboard** review queue is always empty;
- `ai_engine.AIEvaluationRun` has nothing to attach a run to.

Creating evaluations belongs to your modules, not to submissions, so the
submissions side does not do it. Instead it announces the moment a document
becomes reviewable and gets out of the way.

## The hook

`submissions/signals.py` defines:

```python
submission_finalized = django.dispatch.Signal()
```

It is sent from `services/submission_service.finalize()` — the single place a
submission moves to `submitted` — inside that function's transaction:

```python
submission_finalized.send(sender=Submission, submission=submission, actor=user)
```

Arguments:

| name | meaning |
|---|---|
| `submission` | the `Submission` just finalised |
| `actor` | the user who finalised it (may be `None`) |

Subscribe from your own app, e.g. `judging/signals.py` wired in `JudgingConfig.ready()`:

```python
from django.dispatch import receiver
from submissions.signals import submission_finalized

@receiver(submission_finalized)
def queue_for_review(sender, submission, actor, **kwargs):
    rubric = Rubric.objects.filter(
        competition=submission.competition,
        submission_type=submission.submission_type,
        is_active=True,
    ).first()
    if rubric is None:
        return  # no rubric published yet — nothing to score against
    evaluation, created = Evaluation.objects.get_or_create(
        submission=submission, judge=None, rubric=rubric,
    )
    ...
```

Make your receiver **idempotent**: a team may finalise, upload a new version and
finalise again.

## What is already in place for you

- `Submission.is_late` / `late_by` — set automatically against
  `CompetitionSettings.submission_deadline`. Uploads are never blocked.
- `Submission.marking_excluded` / `marking_note` — staff **or a judge** can
  decline to mark a late submission (`/submissions/<pk>/marking/`).
  **Skip these in scoring.**
- `Submission.selection_status` — the registration outcome, set by organisers at
  `/submissions/proposals/`. Only `selected` proposals go on to build.
- `ParsedDocument` — extracted `title`, `sections`, `text`, `tables`, plus
  `DocumentImage` rows holding the actual figures. Feed `parsed.text` to the AI;
  `parsed.images` is how a judge sees the architecture diagram.
- `services/audit_service.record(...)` — please audit judge assignments and score
  overrides with `AuditLog.Action.SCORE_OVERRIDDEN`; the action already exists.
- `CriterionScore.apply_judge_score(value, judge, reason)` — enforces the
  mandatory justification and writes the `ScoreOverride` row.
- `CompetitionSettings.judging_open` — organisers open and close judging from
  the Competition Management screen; gate your write paths on it.

## Not done, and deliberately yours

- Creating evaluations and assigning judges (a review-queue screen).
- Triggering AI runs and copying suggestions into `CriterionScore.ai_score`.
- Aggregating `weighted_total` and building the leaderboard.
