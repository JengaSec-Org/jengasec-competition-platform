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
    # No account yet: nothing to attach an in-app row to, so send directly.
    from django.core.mail import send_mail

    send_mail(
        subject=f"You have been invited to {team.team_name}",
        message=body + "\n\n--\nJengaSec Evaluation Platform",
        from_email=None,
        recipient_list=[email],
        fail_silently=True,
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


def registration_closing(team, hours, outstanding):
    """Registration deadline nearing and this team is not finished."""
    if not team.captain:
        return 0
    deadline = team.competition.settings.registration_deadline
    items = "\n".join(f"  - {item}" for item in outstanding)
    return ns.notify_user(
        team.captain,
        subject=f"Registration closes in {hours} hours -- {team.team_name}",
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


def _today():
    from django.utils import timezone

    return timezone.localdate().isoformat()
