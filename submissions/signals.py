"""Signals the submissions module publishes.

`submission_finalized` is the handoff point to the judging modules
(rubrics / AI evaluation / human judging / score aggregation), which are
owned by another developer. Submissions does not create Evaluations —
it announces that a document is ready for review and stays out of the
way.

    from submissions.signals import submission_finalized

    @receiver(submission_finalized)
    def queue_for_review(sender, submission, actor, **kwargs):
        ...

Fired once per finalise, inside the same transaction, with:
    submission -- the Submission just moved to `submitted`
    actor      -- the user who finalised it (may be None)
"""
import django.dispatch

submission_finalized = django.dispatch.Signal()
