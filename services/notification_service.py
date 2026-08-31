"""Creating in-app notifications.

Views should never build `Notification` rows by hand — go through these
helpers so recipient resolution and the audit-friendly bulk write stay in
one place.

Roles come from `accounts.UserProfile.role`, which is the single source of
truth (it syncs the matching auth Group on save). Querying group names
directly works until someone renames a group, so don't.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q

from accounts.models import UserProfile
from notifications.models import Notification

User = get_user_model()


def notify_user(recipient, subject, body="", link="", notification_type=Notification.Type.GENERAL):
    """Create a single in-app notification for one user."""
    return Notification.objects.create(
        user=recipient,
        subject=subject,
        body=body,
        link=link,
        type=notification_type,
    )


def notify_users(recipients, subject, body="", link="", notification_type=Notification.Type.GENERAL):
    """Create the same notification for many users in one INSERT.

    Returns the created rows. `recipients` may be a queryset or any iterable
    of users; an empty one is a no-op.
    """
    return Notification.objects.bulk_create(
        [
            Notification(
                user=user,
                subject=subject,
                body=body,
                link=link,
                type=notification_type,
            )
            for user in recipients
        ]
    )


def admin_recipients():
    """Organisers: anyone holding the admin role, plus Django staff/superusers.

    Superusers created with `createsuperuser` have no UserProfile, so the
    role check alone would miss them.
    """
    return User.objects.filter(is_active=True).filter(
        Q(profile__role=UserProfile.Role.ADMIN) | Q(is_staff=True) | Q(is_superuser=True)
    ).distinct()


def judge_recipients():
    """Everyone holding the judge role."""
    return User.objects.filter(is_active=True, profile__role=UserProfile.Role.JUDGE)


def notify_admins(subject, body="", link="", notification_type=Notification.Type.GENERAL):
    return notify_users(admin_recipients(), subject, body, link, notification_type)


def notify_judges(subject, body="", link="", notification_type=Notification.Type.GENERAL):
    return notify_users(judge_recipients(), subject, body, link, notification_type)
