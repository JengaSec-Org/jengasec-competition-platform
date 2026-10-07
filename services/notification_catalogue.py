"""The notification catalogue: one function per event the platform announces.

`notification_service` is the plumbing -- recipients, bulk writes, the email
queue. This module is the *content*: who hears about each event, what the
message says, and which template renders the email.

Two groups of functions live here.

**Wired.** Called from `notifications/signals.py` or a management command
when the underlying state changes. Nothing else needs to do anything.

**Published.** The event exists in the competition rules but the flow that
produces it does not exist in the codebase yet -- there is no signup view,
no invitation model, no policy record. Rather than invent those, each is a
function with an explicit signature: whoever builds the flow calls it and
the notification is done. See `docs/HANDOFF-notifications.md`.

Every function is idempotent through `dedupe_key`; calling one twice for
the same event is safe and is the intended way to handle retries.
"""
from django.urls import reverse
from django.utils import timezone

from notifications.models import Notification
from services import notification_service as ns

Type = Notification.Type

# How long organisers tell teams a registration review takes. Surfaced in
# the receipt so teams know when to start chasing.
REVIEW_WINDOW = "3 working days"


def registration_reference(team):
    """The team identifier, e.g. JS26-B-014 (Entry Guide section 10).

    Issued at approval; before that the receipt quotes a provisional
    reference derived from the row so it can still be traced.
    """
    return team.team_identifier or f"REG-{team.competition_id:02d}-{team.pk:04d}"


# ===========================================================================
# Published -- account and invitation flows (no trigger in the codebase yet)
# ===========================================================================

def account_created(user, verification_url, expires_hours=24):
    """A new account needs its email verified.

    Call immediately after creating the user, before any other mail: until
    this is verified the address is unproven and nothing else should be
    sent to it.
    """
    return ns.notify_user(
        user,
        subject="Verify your JengaSec account",
        body=(
            f"Your account is created but not yet active. Verify your email "
            f"address to finish setting it up -- the link expires in "
            f"{expires_hours} hours.\n\n{verification_url}\n\n"
            f"Once verified you can create a team or accept an invitation "
            f"to join one."
        ),
        link=verification_url,
        notification_type=Type.ACCOUNT_CREATED,
        send_email=True,
        dedupe_key="",  # a resent verification is a new message, not a duplicate
    )


def email_verified(user):
    """Verification succeeded: confirm, and point at the next step."""
    return ns.notify_user(
        user,
        subject="Email verified",
        body=(
            "Your email address is confirmed and your account is active.\n\n"
            "Next: create a team and register it for a competition, or accept "
            "an invitation if a captain has already added you to theirs."
        ),
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.EMAIL_VERIFIED,
        send_email=True,
        dedupe_key=f"user:{user.pk}:email_verified",
    )


def invitation_sent(email, team, captain, accept_url, expires_days=7, invitee_user=None):
    """A captain invited somebody to a team (Entry Guide section 6, step 6).

    Addressed to an email, because the invitee usually has no account yet.
    With no account the email is sent directly from here; with one
    (`invitee_user`) the normal in-app + queued-email path is used.
    """
    captain_name = captain.get_full_name() or captain.username if captain else "The captain"
    body = (
        f"{captain_name} has invited you to join {team.team_name} for "
        f"{team.competition.name}.\n\n"
        f"Accept here: {accept_url}\n\n"
        f"The invitation expires in {expires_days} days. You will need a "
        f"JengaSec account to accept it -- you can create one from that link "
        f"if you do not have one yet."
    )
    if invitee_user is not None:
        return ns.notify_user(
            invitee_user,
            subject=f"You have been invited to {team.team_name}",
            body=body,
            link=accept_url,
            notification_type=Type.INVITATION_SENT,
            send_email=True,
            dedupe_key="",  # a re-invitation is deliberate
        )
    # No account yet: nothing to attach an in-app row to, so send directly --
    # branded like every other message, since this is the first thing the
    # invitee ever sees from JengaSec.
    from services.email_render import send_branded

    send_branded(
        f"You have been invited to {team.team_name}",
        body,
        email,
        link=accept_url,
        kicker="Team invitation",
        action_label="Accept the invitation",
        note=f"This invitation expires in {expires_days} days. You will need a "
             f"JengaSec account under this exact address to accept it.",
    )
    return {"to": email, "subject": f"You have been invited to {team.team_name}", "body": body}


def invitation_accepted(team, member, outstanding=()):
    """Somebody joined: tell the captain, and what that member still owes."""
    if not team.captain:
        return 0
    name = getattr(member, "student_name", None) or str(member)
    if outstanding:
        remaining = "Still outstanding for them:\n" + "\n".join(f"  - {i}" for i in outstanding)
    else:
        remaining = "Nothing outstanding for them."
    return ns.notify_user(
        team.captain,
        subject=f"{name} joined {team.team_name}",
        body=f"{name} has accepted your invitation.\n\n{remaining}",
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.INVITATION_ACCEPTED,
        send_email=True,
        dedupe_key=f"team:{team.pk}:joined:{getattr(member, 'pk', name)}",
    )


def policy_updated(users, version, summary, accept_url):
    """A policy changed and everyone has to re-accept it.

    Not deduplicated per user by version alone -- the key includes the
    version, so each new version notifies once and only once.
    """
    return ns.notify_users(
        users,
        subject=f"Updated competition policy ({version}) -- action needed",
        body=(
            f"The competition policy has been updated to version {version}.\n\n"
            f"What changed:\n{summary}\n\n"
            f"You need to read and accept the new version to keep taking part."
        ),
        link=accept_url,
        notification_type=Type.POLICY_UPDATED,
        send_email=True,
        dedupe_key=f"policy:{version}:accept",
    )


# ===========================================================================
# Wired -- driven by signals and commands in this module
# ===========================================================================

def registration_submitted(team):
    """Receipt for a submitted registration, to everyone on the team.

    Keyed on the submission time, so a registration corrected and
    resubmitted after a rejection gets a fresh receipt.
    """
    members = ", ".join(m.student_name for m in team.members.all()) or "no members yet"
    prefs = ", ".join(team.track_preference_labels) or team.track_label or "not set"
    stamp = team.submitted_at.isoformat(timespec="microseconds") if team.submitted_at else "draft"
    return ns.notify_team(
        team,
        subject=f"Registration received: {team.team_name}",
        body=(
            f"Reference {registration_reference(team)}\n\n"
            f"Registered for: {team.competition.name}\n"
            f"Team: {team.team_name} ({team.get_team_type_display()})\n"
            f"Track preference: {prefs}\n"
            f"Institution: {team.institution or 'not set'}\n"
            f"Members: {members}\n\n"
            f"The registration is now locked while organisers review it -- "
            f"within {REVIEW_WINDOW}. You will be told the outcome either way. "
            f"Quote the reference above in any correspondence."
        ),
        notification_type=Type.REGISTRATION_SUBMITTED,
        dedupe_key=f"team:{team.pk}:registration_submitted:{stamp}",
    )


def registration_approved(team, assignment=None, resources=()):
    """Approved and assigned a cell. The one email a team keeps.

    Carries everything needed to start work: identifier, cell, brief,
    and whatever access the organisers have recorded.
    """
    lines = [
        f"{team.team_name} is approved for {team.competition.name}.",
        "",
        f"Team identifier: {registration_reference(team)}",
        f"Track: {team.track_label or 'not set'}",
    ]
    if team.cell_id:
        lines.append(f"Cell: {team.cell_label}")
    elif team.submits_proposal:
        lines.append(
            "Cell: assigned when your proposal is selected. Places are limited "
            "and awarded competitively; submitting a proposal is not a place."
        )
    if team.application_brief_id:
        lines.append(f"Brief: {team.application_brief.code} -- {team.application_brief.name}")
    elif team.responsibility:
        lines.append(f"Your component: {team.responsibility}")
    if team.enterprise and not team.cell_id:
        lines.append(f"Enterprise: {team.get_enterprise_display()}")

    if team.repository_url or team.namespace:
        lines += ["", "Welcome pack:"]
        if team.repository_url:
            lines.append(f"  Repository: {team.repository_url}")
        if team.namespace:
            lines.append(f"  Namespace: {team.namespace}")

    if resources:
        lines += ["", "Provided for your track:"] + [f"  - {r}" for r in resources]

    if assignment is not None:
        lines += ["", "Engagement access:"]
        if assignment.endpoint:
            lines.append(f"  Endpoint: {assignment.endpoint}")
        if assignment.access_notes:
            lines.append(f"  {assignment.access_notes}")

    settings_row = team.competition.settings
    deadlines = [
        ("Registration closes", settings_row.registration_deadline),
        ("Build deadline", settings_row.build_deadline),
        ("Proposal deadline", settings_row.proposal_deadline),
        ("Submission deadline", settings_row.submission_deadline),
    ]
    known = [(label, when) for label, when in deadlines if when]
    if known:
        lines += ["", "Deadlines:"]
        lines += [f"  {label}: {when:%d %b %Y, %H:%M}" for label, when in known]

    lines += [
        "",
        "Credentials and repository access are issued separately by the "
        "organisers and are never sent in this message.",
    ]

    return ns.notify_team(
        team,
        subject=f"{team.team_name} approved -- {team.cell_id or 'cell pending'}",
        body="\n".join(lines),
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.REGISTRATION_APPROVED,
        dedupe_key=f"team:{team.pk}:approved",
    )


def registration_rejected(team):
    """Turned down, with the reason and the time left to fix it."""
    if not team.captain:
        return 0
    reason = team.rejection_reason or "No reason was recorded. Contact the organisers."
    deadline = team.competition.settings.registration_deadline
    if deadline:
        window = (
            f"Registration closes on {deadline:%d %b %Y at %H:%M}. If you correct "
            f"the above before then, you can resubmit."
        )
    else:
        window = "Contact the organisers about resubmitting."
    return ns.notify_user(
        team.captain,
        subject=f"{team.team_name}: registration not accepted",
        body=(
            f"Reference {registration_reference(team)}\n\n"
            f"Reason:\n{reason}\n\n{window}"
        ),
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.REGISTRATION_REJECTED,
        send_email=True,
        dedupe_key=f"team:{team.pk}:rejected",
    )


def complete_your_details(user, items, policy_url=""):
    """One-off push to finish capturing a competitor's own details.

    Distinct from `outstanding_items`, the daily digest: that chases a
    team member about their place on a team, this explains *why* the
    platform is asking for the data at all and on what basis it is held.
    Competitors on an already-submitted registration get it too -- venue
    access depends on the same record, and profile editing is not locked.
    """
    if not items:
        return None
    listed = "\n".join(f"  - {item}" for item in items)
    policy = policy_url or "https://strathmore.edu/privacy-policy/"
    return ns.notify_user(
        user,
        subject="Finish setting up your JengaSec account",
        body=(
            "Your account is not yet complete. We need every competitor's "
            "details captured in full before the event: they confirm you are "
            "eligible, they put you on the right team, and they are what gets "
            "you through the gate at Strathmore on the day.\n\n"
            f"Still outstanding for you:\n{listed}\n\n"
            "All of it is handled under the Strathmore University Data Privacy "
            "Policy and the Data Protection Act 2019. In short: the data is used "
            "to run this competition and nothing else, it is seen by organisers "
            "and judges rather than published, and it is kept only as long as the "
            "season and its appeals need. You can ask to see, correct or object to "
            "any of it by writing to dpo@strathmore.edu.\n\n"
            f"The full policy: {policy}\n"
            "The notice specific to this competition is in the code of conduct on "
            "the platform, which you are asked to accept before submitting.\n\n"
            "Open your profile to fill in what is missing."
        ),
        link=reverse("accounts:profile"),
        notification_type=Type.OUTSTANDING_ITEMS,
        send_email=True,
        # One request per person; re-running the sweep does not re-send.
        dedupe_key=f"complete-details:{user.pk}",
    )


def briefing(user, *, items, dates, team=None, identity_lost=False, extra="",
             matching="", slug="brief"):
    """Everything one competitor needs to know, in a single email.

    The alternative was four sends in a day -- complete your details,
    re-upload your ID, the new deadlines, the privacy notice -- which is
    how people learn to ignore mail from the organisers. The alternative
    to *that* is one broadcast, which cannot say what any particular
    person still owes, and so gets ignored by everyone who has already
    done it.

    This is one email per person, assembled from their own record: their
    outstanding items, their own missing ID, the dates that apply to
    their team. Someone with nothing outstanding gets the dates and
    nothing that reads like a telling-off.

    `slug` keys the deduplication, so a later briefing with a different
    slug is a new message rather than a suppressed duplicate.
    """
    lines = []

    if dates:
        listed = "\n".join(
            f"  - {d['label']}: {timezone.localtime(d['when']):%a %d %b %Y at %H:%M} (EAT)"
            for d in dates if not d["passed"]
        )
        if listed:
            lines.append("The dates that matter:\n" + listed)

    if identity_lost:
        lines.append(
            "Your identity document is no longer on file and has to be uploaded "
            "again. That is our fault, not yours: the file was lost when the "
            "platform moved to its permanent storage and cannot be recovered."
        )

    # Placed before the checklist: for somebody with no team, or a captain a
    # seat short, this is the item the rest of the list depends on.
    if matching:
        lines.append(matching)

    if items:
        listed = "\n".join(f"  - {item}" for item in items)
        lines.append(
            "Before the registration deadline, your account still needs:\n" + listed
        )
    else:
        lines.append(
            "Your own details are complete -- there is nothing outstanding for you."
        )

    if extra:
        lines.append(extra)

    lines.append(
        "Why we ask for this: the details confirm you are eligible, place you on "
        "the right team, and are what gets you through the gate at Strathmore on "
        "the day. They are held by Strathmore University under its Data Privacy "
        "Policy and the Data Protection Act 2019, used only to run this "
        "competition, and seen by organisers and judges rather than published. "
        "Section 7 of the code of conduct on the platform is the full notice. To "
        "see, correct or object to anything we hold, write to dpo@strathmore.edu.\n\n"
        "https://strathmore.edu/privacy-policy/"
    )

    lines.append("Open your profile on the platform to fill in anything missing.")

    return ns.notify_user(
        user,
        subject=f"JengaSec: what you need to do before registration closes",
        body="\n\n".join(lines),
        link=reverse("accounts:profile"),
        notification_type=Type.OUTSTANDING_ITEMS,
        send_email=True,
        dedupe_key=f"briefing:{slug}:{user.pk}",
    )


def announcement(team, subject, body, slug):
    """An organiser's own message to one side of the competition.

    Everything else in this module is an event the platform noticed.
    This is the case where a human has something to say -- a delay, a
    venue change -- and needs it to reach teams who are not logged in.
    """
    return ns.notify_team(
        team,
        subject=subject,
        body=body,
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.GENERAL,
        dedupe_key=f"announce:{slug}:{team.pk}",
    )


def registration_returned(team, note=""):
    """Handed back for edits rather than refused.

    A rejection and a return are different messages and must not share
    wording: this one says the registration is unlocked, what to change
    and that resubmitting is expected. Called straight from the view --
    `notifications.signals` only reacts to APPROVED and REJECTED, since a
    return lands on the same REGISTERED status a team starts life in.

    The dedupe key carries the note, so a second return for a different
    reason is delivered rather than suppressed as a duplicate.
    """
    if not team.captain:
        return 0
    reason = note or team.rejection_reason or "Contact the organisers for details."
    deadline = team.competition.settings.registration_deadline
    if deadline:
        window = (
            f"Registration closes on {deadline:%d %b %Y at %H:%M} (EAT) -- "
            f"resubmit before then and it will be reviewed again."
        )
    else:
        window = "Resubmit once it is corrected."
    return ns.notify_user(
        team.captain,
        subject=f"{team.team_name}: registration returned for changes",
        body=(
            f"Reference {registration_reference(team)}\n\n"
            f"Your registration has been returned so you can edit it. It has "
            f"not been refused, and your roster is unlocked again.\n\n"
            f"What to change:\n{reason}\n\n{window}"
        ),
        link=reverse("accounts:team_requests"),
        notification_type=Type.REGISTRATION_REJECTED,
        send_email=True,
        # sha256, not hash(): string hashing is salted per process, so
        # built-in hash() would give a different key on every restart and
        # the suppression would stop working.
        dedupe_key=f"team:{team.pk}:returned:{_digest(reason)}",
    )


def registration_closing(team, hours, outstanding, remaining=None):
    """Registration deadline nearing and this team is not finished.

    `hours` is the threshold that fired and keys the deduplication;
    `remaining` is the real gap, which is what the subject quotes.
    """
    if not team.captain:
        return 0
    deadline = team.competition.settings.registration_deadline
    items = "\n".join(f"  - {item}" for item in outstanding)
    when = time_left(remaining) if remaining is not None else f"in {hours} hours"
    return ns.notify_user(
        team.captain,
        subject=f"Registration closes {when} -- {team.team_name}",
        body=(
            f"Registration for {team.competition.name} closes on "
            f"{deadline:%d %b %Y at %H:%M}.\n\n"
            f"{team.team_name} is not complete. Outstanding:\n{items}\n\n"
            f"Teams that are still incomplete when registration closes are not "
            f"allocated a cell."
        ),
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.REGISTRATION_CLOSING,
        send_email=True,
        dedupe_key=f"team:{team.pk}:closing:{hours}h",
    )


def outstanding_items(user, team, items):
    """Daily nudge listing exactly what one member still has to do.

    Suppressed by the caller once `items` is empty -- an empty digest is
    the thing that teaches people to ignore the daily mail.
    """
    if not items:
        return None
    listed = "\n".join(f"  - {item}" for item in items)
    return ns.notify_user(
        user,
        subject=f"Outstanding for {team.team_name}",
        body=(
            f"These are still missing for your place on {team.team_name}:\n\n"
            f"{listed}\n\nThey stop being listed here as soon as they are done."
        ),
        link=reverse("dashboard:main_dashboard"),
        notification_type=Type.OUTSTANDING_ITEMS,
        send_email=True,
        # One digest per member per day, whatever else runs the command.
        dedupe_key=f"digest:{user.pk}:{team.pk}:{_today()}",
    )


def identity_document_lost(user):
    """Their ID upload is gone from storage and has to be taken again.

    Not their mistake and not a rejection, so the wording says so
    plainly: an apology, the reason, and one thing to do. Anything that
    reads like "your document was invalid" produces a support queue of
    people asking what they did wrong.

    The caller clears `identity_document` first, which puts the item
    back on the profile checklist and the daily digest -- so this mail
    is a heads-up, not the only route back.
    """
    return ns.notify_user(
        user,
        subject="Please re-upload your ID document",
        body=(
            "Your identity document is no longer on file and we need you to "
            "upload it again. This is our fault, not yours -- the file was "
            "lost when the platform moved to its permanent storage, and it "
            "cannot be recovered. Nothing else on your profile was affected "
            "and your registration is not at risk.\n\n"
            "Open your profile and upload your student or national ID again: "
            "PDF, JPG or PNG, up to 5 MB. It is seen only by organisers and "
            "is used to confirm your student status.\n\n"
            "Sorry for the inconvenience."
        ),
        link=reverse("accounts:profile"),
        notification_type=Type.OUTSTANDING_ITEMS,
        send_email=True,
        # One request per person, however many times the sweep is run.
        dedupe_key=f"identity-reupload:{user.pk}",
    )


def membership_changed(team, member_name, member_user=None, withdrew=False):
    """Somebody left a team. Both sides hear, and both hear the consequence."""
    settings_row = team.competition.settings
    remaining = team.members.count()
    grace = settings_row.member_grace_days
    verb = "withdrawn from" if withdrew else "been removed from"

    captain_body = (
        f"{member_name} has {verb} {team.team_name}.\n\n"
        f"The team now has {remaining} member{'s' if remaining != 1 else ''}. "
        f"You have {grace} days to replace them before the registration is "
        f"reviewed again."
    )
    sent = 0
    if team.captain:
        ns.notify_user(
            team.captain,
            subject=f"{member_name} left {team.team_name}",
            body=captain_body,
            link=reverse("dashboard:main_dashboard"),
            notification_type=Type.MEMBERSHIP_CHANGED,
            send_email=True,
            dedupe_key=f"team:{team.pk}:left:{member_name}:captain",
        )
        sent += 1

    if member_user is not None:
        ns.notify_user(
            member_user,
            subject=f"You are no longer part of {team.team_name}",
            body=(
                f"You have {verb.replace('been ', '')} {team.team_name} "
                f"({team.competition.name}).\n\n"
                f"You keep your JengaSec account and can join or create another "
                f"team while registration is open."
            ),
            link=reverse("dashboard:main_dashboard"),
            notification_type=Type.MEMBERSHIP_CHANGED,
            send_email=True,
            dedupe_key=f"team:{team.pk}:left:{member_name}:member",
        )
        sent += 1
    return sent


def submission_returned_incomplete(submission, check):
    """The structure check found mandatory front matter missing (Guide
    section 9): the proposal goes back to the team with the list."""
    missing = "\n".join(f"  - {name}" for name in check.missing_front_matter)
    sections = [s["name"] for s in check.missing_sections]
    extra = ""
    if sections:
        extra = (
            "\n\nAlso not found (each scores zero for its criterion unless added):\n"
            + "\n".join(f"  - {name}" for name in sections)
        )
    deadline = submission.competition.settings.deadline_for_type(
        submission.submission_type.name
    )
    window = (
        f"Upload a corrected version and submit again before "
        f"{deadline:%d %b %Y at %H:%M}." if deadline else "Upload a corrected version and submit again."
    )
    return ns.notify_team(
        submission.team,
        subject=f"Returned incomplete: {submission.submission_type.name}",
        body=(
            f"Version {submission.current_version} of your {submission.submission_type.name} "
            f"is missing mandatory front matter and has not been queued for review:\n\n"
            f"{missing}{extra}\n\n{window}"
        ),
        link=reverse("submissions:detail", args=[submission.pk]),
        notification_type=Type.SUBMISSION_CONFIRMED,
        dedupe_key=f"submission:{submission.pk}:v{submission.current_version}:returned",
    )


def _today():
    from django.utils import timezone

    return timezone.localdate().isoformat()


def _digest(text, length=12):
    """A short, process-stable fingerprint for a dedupe key."""
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def time_left(delta):
    """How long is left, in words, for a subject line.

    The reminders fire at fixed thresholds but can run well after one is
    crossed, so quoting the threshold tells competitors the wrong thing:
    a 72-hour notice sent with 48 hours to go said "in 72 hours". This
    describes the actual gap, while the threshold stays as the dedupe
    key so once-only delivery is unaffected.
    """
    seconds = max(int(delta.total_seconds()), 0)
    hours = seconds // 3600
    if hours < 1:
        return "in under an hour"
    if hours < 48:
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"in {days} days"
