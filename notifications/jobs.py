"""The notification jobs, in one list, with how to preview and run each.

Two callers need the same answer: the staff console at
`notifications:console`, where an organiser clicks a button, and the
scheduled runner at `/tasks/run/`, which a cron hits hourly. Keeping the
list here means adding a job does not involve remembering a second
place, and the console cannot offer something the scheduler silently
skips.

Each job is a management command, which stays the single implementation
-- the console shells into the same code a shell session would, and
captures its output, rather than reimplementing the logic behind a view.
"""
from dataclasses import dataclass
from io import StringIO

from django.core.management import call_command


@dataclass(frozen=True)
class Job:
    key: str
    label: str
    description: str
    command: str
    # Most commands preview with --dry-run and send with no flag. The
    # destructive ones invert it: harmless by default, acting only on
    # --confirm. Both shapes are described rather than assumed.
    preview_args: tuple = ("--dry-run",)
    run_args: tuple = ()
    # Whether the hourly scheduled runner should include it. One-off
    # announcements and data requests are deliberately left out: they go
    # when a person decides, not on a timer.
    scheduled: bool = False
    # Shown with a stronger confirmation in the console.
    warning: str = ""

    def preview(self):
        return self._run(self.preview_args)

    def run(self):
        return self._run(self.run_args)

    def _run(self, args):
        out = StringIO()
        call_command(self.command, *args, stdout=out, stderr=out)
        return out.getvalue().strip()


JOBS = (
    Job(
        key="registration_reminders",
        label="Registration closing reminders",
        description=(
            "Warns captains of incomplete teams that registration is about to "
            "close, at 72, 48, 24 and 12 hours out. One notice per threshold."
        ),
        command="send_registration_reminders",
        scheduled=True,
    ),
    Job(
        key="deadline_reminders",
        label="Proposal and submission deadline reminders",
        description=(
            "Warns teams that have not filed in the window being chased. "
            "Proposals and submissions are chased separately."
        ),
        command="send_deadline_reminders",
        scheduled=True,
    ),
    Job(
        key="outstanding_digest",
        label="Daily outstanding digest",
        description=(
            "Emails each team member the items they personally still owe. "
            "Members with nothing outstanding are skipped."
        ),
        command="send_outstanding_digest",
        scheduled=True,
    ),
    Job(
        key="email_queue",
        label="Send queued emails",
        description=(
            "Delivers notifications still waiting for their email. Most are "
            "sent on commit already; this clears anything that failed."
        ),
        command="send_notification_emails",
        scheduled=True,
    ),
    Job(
        key="briefing",
        label="Send everyone a single combined briefing",
        description=(
            "One email per competitor covering the deadlines, their own "
            "outstanding items, a lost ID if theirs is missing, and the data "
            "privacy notice. Use this instead of sending those separately. "
            "Reconciles ID records with storage first, so the lists are true."
        ),
        command="send_briefing",
        preview_args=(),
        run_args=("--confirm",),
        warning=(
            "Reaches every competitor and clears ID references whose file is "
            "missing from storage. Preview first and read the sample."
        ),
    ),
    Job(
        key="data_completion",
        label="Ask competitors to complete their details",
        description=(
            "One-off request to everyone with gaps in their profile, including "
            "members of already-submitted teams, with the data-privacy notice. "
            "Sent once per person."
        ),
        command="send_data_completion_notice",
        preview_args=(),
        run_args=("--confirm",),
    ),
    Job(
        key="identity_reupload",
        label="Ask for identity documents again",
        description=(
            "Finds ID records whose file is missing from storage, clears the "
            "stale row so the checklist chases it, and asks the owner to upload "
            "again."
        ),
        command="request_identity_reupload",
        preview_args=(),
        run_args=("--confirm",),
        warning=(
            "This clears the stored reference for every document missing from "
            "storage, which cannot be undone. Check the file store is working "
            "first."
        ),
    ),
)

BY_KEY = {job.key: job for job in JOBS}
SCHEDULED = tuple(job for job in JOBS if job.scheduled)


def get(key):
    return BY_KEY.get(key)


def run_scheduled():
    """Run every scheduled job, collecting output per job.

    One job failing must not stop the rest: a broken digest should not
    also stop the deadline reminders going out.
    """
    results = {}
    for job in SCHEDULED:
        try:
            results[job.key] = {"ok": True, "output": job.run()}
        except Exception as exc:  # noqa: BLE001 — reported, not raised
            results[job.key] = {"ok": False, "output": f"{type(exc).__name__}: {exc}"}
    return results
