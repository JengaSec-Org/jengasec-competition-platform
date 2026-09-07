"""Template context for the notification badge.

One indexed COUNT per page for signed-in users, served by
`ix_notification_unread`; anonymous visitors short-circuit without a query.
"""


def unread_notifications(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"unread_notifications": 0}
    return {
        "unread_notifications": user.notifications.filter(is_read=False).count()
    }
