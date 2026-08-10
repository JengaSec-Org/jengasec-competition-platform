from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect

from .models import Notification


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()
    return render(request, "notifications/list.html", {"notifications": notifications})


@login_required
def mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return redirect(notification.link or "notifications:list")


@login_required
def unread_count(request):
    count = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({"unread": count})