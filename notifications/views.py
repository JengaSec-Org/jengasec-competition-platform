from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .models import Notification


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()
    return render(request, "notifications/list.html", {"notifications": notifications})


@login_required
def notification_read(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    if request.method == "POST":
        notification.mark_read()
    return redirect("notifications:list")