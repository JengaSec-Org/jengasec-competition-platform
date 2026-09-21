"""Creating and delivering notifications.

Two channels, one record. Every notification is an in-app row; those owed
an email carry `send_email=True`. They are delivered as soon as the
surrounding transaction commits (`deliver_after_commit`), off the request
thread when a real SMTP backend is configured, and anything that fails
stays queued for the `send_notification_emails` command to retry --
creating a notification never blocks on SMTP, and a mail server outage
never fails a submission.

Views and signal handlers should not build `Notification` rows by hand:
recipient resolution and duplicate suppression live here.

Roles come from `accounts.UserProfile.role`, the declared source of truth
(it syncs the matching auth Group on save). Querying group names directly
works right up until someone renames a group.
"""
import logging
import threading

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage, get_connection
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import UserProfile
from notifications.models import Notification

logger = logging.getLogger(__name__)
User = get_user_model()

# Give up on an address after this many failed sends, so one dead mailbox
# does not make every drain run slower than the last.
MAX_SEND_ATTEMPTS = 5

# Backends that cost nothing to call: deliver inline rather than on a thread.
_INLINE_BACKENDS = ("console", "locmem", "dummy", "filebased")


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------

def team_recipients(team):
    """Platform accounts belonging to a team.

    Mirrors `submission_service.teams_for()` from the other direction:
    captain, linked members, and members matched by email address. A team
    may have members who have not registered an account yet -- they simply
    do not appear here.
    """
    query = Q(captained_teams=team) | Q(team_memberships__team=team)
    for email in team.members.values_list("email", flat=True):
        if email:
            query |= Q(email__iexact=email)
    return User.objects.filter(is_active=True).filter(query).distinct()


def admin_recipients():
    """Organisers: the admin role, plus Django staff and superusers.

    A superuser made with `createsuperuser` has no UserProfile, so the role
    check alone would miss them.
    """
    return (
        User.objects.filter(is_active=True)
        .filter(
            Q(profile__role=UserProfile.Role.ADMIN)
            | Q(is_staff=True)
            | Q(is_superuser=True)
        )
        .distinct()
    )


def judge_recipients():
    """Everyone holding the judge role."""
    return User.objects.filter(is_active=True, profile__role=UserProfile.Role.JUDGE)


# ---------------------------------------------------------------------------
# Creating notifications
# ---------------------------------------------------------------------------

def notify_user(
    user,
    subject,
    body="",
    link="",
    notification_type=Notification.Type.GENERAL,
    send_email=False,
    dedupe_key="",
):
    """Create one notification. Returns it, or None if it was a duplicate.

    A `dedupe_key` collision is not an error: it means this exact
    notification already exists for this user, which is precisely what the
    key is for.
    """
    try:
        # Savepoint, so a duplicate does not poison an enclosing transaction.
        with transaction.atomic():
            notification = Notification.objects.create(
                user=user,
                subject=subject,
                body=body,
                link=link,
                type=notification_type,
                send_email=send_email,
                dedupe_key=dedupe_key,
            )
    except IntegrityError:
        logger.debug("Duplicate notification suppressed: %s / %s", user, dedupe_key)
        return None
    if send_email:
        deliver_after_commit([notification.pk])
    return notification


def notify_users(
    recipients,
    subject,
    body="",
    link="",
    notification_type=Notification.Type.GENERAL,
    send_email=False,
    dedupe_key="",
):
    """Create the same notification for many users in one INSERT.

    `ignore_conflicts` makes this idempotent against `dedupe_key`: a repeat
    run inserts the users who were missing and silently skips the rest.
    Returns the number of recipients considered.
    """
    rows = [
        Notification(
            user=user,
            subject=subject,
            body=body,
            link=link,
            type=notification_type,
            send_email=send_email,
            dedupe_key=dedupe_key,
        )
        for user in recipients
    ]
    if not rows:
        return 0
    Notification.objects.bulk_create(rows, ignore_conflicts=True)
    if send_email:
        # bulk_create with ignore_conflicts returns no pks on every backend,
        # so deliver by key: whatever of this batch is still unsent.
        deliver_after_commit(
            dedupe_key=dedupe_key,
            user_ids=[u.pk for u in recipients] if not dedupe_key else None,
            subject=subject if not dedupe_key else None,
        )
    return len(rows)


def notify_team(
    team,
    subject,
    body="",
    link="",
    notification_type=Notification.Type.GENERAL,
    dedupe_key="",
):
    """Notify a team -- in-app *and* by email.

    Teams are the audience that needs to hear things while they are not
    logged in, so team notifications always carry an email.
    """
    return notify_users(
        team_recipients(team),
        subject,
        body=body,
        link=link,
        notification_type=notification_type,
        send_email=True,
        dedupe_key=dedupe_key,
    )


def notify_admins(
    subject,
    body="",
    link="",
    notification_type=Notification.Type.GENERAL,
    dedupe_key="",
):
    """Notify organisers -- in-app only (see the module docstring on volume)."""
    return notify_users(
        admin_recipients(),
        subject,
        body=body,
        link=link,
        notification_type=notification_type,
        dedupe_key=dedupe_key,
    )


def notify_judges(
    subject,
    body="",
    link="",
    notification_type=Notification.Type.GENERAL,
    dedupe_key="",
):
    """Notify judges -- in-app only."""
    return notify_users(
        judge_recipients(),
        subject,
        body=body,
        link=link,
        notification_type=notification_type,
        dedupe_key=dedupe_key,
    )


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------

def render_email_body(notification):
    """Body text for one notification.

    Uses `notifications/email/<type>.txt` when a template exists for the
    type, and falls back to a generic one, so a new notification type is
    never undeliverable just because nobody wrote its template yet.
    """
    context = {
        "notification": notification,
        "user": notification.user,
        "site_url": getattr(settings, "SITE_URL", ""),
    }
    for name in (
        f"notifications/email/{notification.type}.txt",
        "notifications/email/default.txt",
    ):
        try:
            return render_to_string(name, context)
        except TemplateDoesNotExist:
            continue
    return notification.body


def pending_email_queryset():
    """Notifications owed an email, oldest first."""
    return (
        Notification.objects.filter(
            send_email=True,
            sent_at__isnull=True,
            send_attempts__lt=MAX_SEND_ATTEMPTS,
        )
        .exclude(user__email="")
        .select_related("user")
        .order_by("pk")
    )


def deliver_after_commit(pks=None, *, dedupe_key=None, user_ids=None, subject=None):
    """Send the emails just queued, once the enclosing transaction commits.

    Without this the queue only drains when the cron command runs -- which
    on a laptop, or a host with no scheduler, is never. Verification links
    and invitations cannot wait for that. With a real SMTP backend the send
    happens on a daemon thread so the request returns at once; console and
    in-memory backends are called inline. Failures are logged and left in
    the queue for the cron retry, exactly as before.

    Disabled with `NOTIFICATION_EMAIL_ON_COMMIT = False` (pure cron mode).
    """
    if not getattr(settings, "NOTIFICATION_EMAIL_ON_COMMIT", True):
        return
    filters = {}
    if pks:
        filters["pk__in"] = list(pks)
    elif dedupe_key:
        filters["dedupe_key"] = dedupe_key
    elif user_ids:
        filters["user_id__in"] = list(user_ids)
        if subject:
            filters["subject"] = subject
    else:
        return

    def _send():
        try:
            send_pending_emails(queryset=pending_email_queryset().filter(**filters))
        except Exception:  # noqa: BLE001 -- never let delivery break a request
            logger.exception("Post-commit email delivery failed")
        finally:
            close_old_connections()

    def _on_commit():
        if any(name in settings.EMAIL_BACKEND for name in _INLINE_BACKENDS):
            _send()
        else:
            threading.Thread(target=_send, name="notification-email", daemon=True).start()

    transaction.on_commit(_on_commit)


def send_pending_emails(limit=None, dry_run=False, queryset=None):
    """Send queued notification emails. Returns (sent, failed, skipped).

    One SMTP connection for the whole batch. A single bad address is
    counted and left for the next run rather than aborting the batch --
    `send_attempts` eventually retires it.
    """
    queue = pending_email_queryset() if queryset is None else queryset
    if limit:
        queue = queue[:limit]
    notifications = list(queue)
    if not notifications:
        return 0, 0, 0

    if dry_run:
        return 0, 0, len(notifications)

    sent = failed = 0
    connection = get_connection()
    try:
        connection.open()
        for notification in notifications:
            message = EmailMessage(
                subject=notification.subject,
                body=render_email_body(notification),
                to=[notification.user.email],
                connection=connection,
            )
            try:
                message.send(fail_silently=False)
            except Exception:
                notification.send_attempts += 1
                notification.save(update_fields=["send_attempts"])
                failed += 1
                logger.exception(
                    "Notification %s: email to %s failed (attempt %s)",
                    notification.pk,
                    notification.user.email,
                    notification.send_attempts,
                )
                continue

            notification.sent_at = timezone.now()
            notification.send_attempts += 1
            notification.save(update_fields=["sent_at", "send_attempts"])
            sent += 1
    finally:
        connection.close()

    return sent, failed, 0
