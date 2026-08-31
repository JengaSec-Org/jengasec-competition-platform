"""In-app notification list, read-receipts, and the unread badge count."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()
    return render(
        request,
        "notifications/list.html",
        {
            "notifications": notifications,
            "unread_total": notifications.filter(is_read=False).count(),
        },
    )


@login_required
@require_POST
def mark_read(request, pk):
    """Mark one notification read, then follow its link.

    POST-only: this changes state, so a GET (a prefetching browser, a crawler,
    an <img> tag in someone's profile) must not be able to trigger it.
    """
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.mark_read()

    # `link` is stored text. Anything that would leave the site is dropped
    # rather than followed — an admin-composed notification is not a licence
    # to redirect a judge to an arbitrary host.
    target = notification.link
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(reverse("notifications:list"))


@login_required
def unread_count(request):
    """Feeds the navbar badge."""
    count = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({"unread": count})
