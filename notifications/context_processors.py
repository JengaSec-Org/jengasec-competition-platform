"""Expose the current user's unread notification count to every template.

Usage in templates:
    {% if unread_notification_count %}<span class="badge">{{ unread_notification_count }}</span>{% endif %}
"""


def unread_notification_count(request):
    if request.user.is_authenticated:
        return {
            "unread_notification_count": request.user.notifications.filter(
                is_read=False
            ).count()
        }
    return {"unread_notification_count": 0}