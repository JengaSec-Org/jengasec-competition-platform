"""The notification dashboard: list, read-receipts, and the unread badge."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification

UNREAD_FILTER = "unread"


@login_required
def notification_list(request):
    """Everything addressed to the signed-in user, newest first.

    `?filter=unread` narrows to the unread ones; the counts stay whole
    either way so the tabs can show both numbers.
    """
    everything = request.user.notifications.all()
    unread_total = everything.filter(is_read=False).count()

    active_filter = request.GET.get("filter", "")
    notifications = (
        everything.filter(is_read=False)
        if active_filter == UNREAD_FILTER
        else everything
    )

    return render(
        request,
        "notifications/list.html",
        {
            "notifications": notifications,
            "unread_total": unread_total,
            "total": everything.count(),
            "active_filter": active_filter,
        },
    )


@login_required
@require_POST
def mark_read(request, pk):
    """Mark one notification read, then follow its link.

    POST-only: this changes state, so a GET -- a prefetching browser, a
    crawler, an <img> tag somewhere else -- must not be able to trigger it.
    """
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.mark_read()

    # `link` is stored text, and organisers can compose notifications. A
    # notification is not a licence to redirect a judge to another host.
    target = notification.link
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(reverse("notifications:list"))


@login_required
@require_POST
def mark_all_read(request):
    updated = request.user.notifications.filter(is_read=False).update(is_read=True)
    if updated:
        messages.success(request, f"{updated} notification(s) marked as read.")
    return redirect(reverse("notifications:list"))


@login_required
def unread_count(request):
    """Feeds the sidebar badge."""
    count = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({"unread": count})
