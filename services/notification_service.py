from django.contrib.auth import get_user_model
from django.db.models import Q

from notifications.models import Notification

User = get_user_model()


def notify_user(recipient, title, message="", link="", notification_type="general"):
    """Create a single in-app notification for one user."""
    return Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        link=link,
        notification_type=notification_type,
    )


def notify_admins(title, message="", link="", notification_type="general"):
    """Notify every admin (staff/superuser)."""
    admins = User.objects.filter(is_active=True).filter(Q(is_staff=True) | Q(is_superuser=True))
    for admin_user in admins:
        notify_user(admin_user, title, message, link, notification_type)


def notify_judges(title, message="", link="", notification_type="general"):
    """Notify everyone in the judge group."""
    judges = User.objects.filter(is_active=True, groups__name="judge")
    for judge_user in judges:
        notify_user(judge_user, title, message, link, notification_type)