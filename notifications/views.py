"""The notification dashboard: list, read-receipts, and the unread badge."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import AnnouncementForm
from .models import Announcement, Notification

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


@login_required
def email_preview(request, notification_type=None):
    """Staff-only rendering of a branded email, for checking the design.

    Email clients are unforgiving and unpreviewable from the code, so the
    templates need somewhere to be looked at. Uses a real notification of
    the requested type when one exists, and a representative sample
    otherwise, so the layout is exercised with the shapes the catalogue
    actually produces: field rows, bullet lists and prose.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        raise Http404("Not found")

    from services import email_render

    types = [t for t, _ in Notification.Type.choices]
    notification_type = notification_type or request.GET.get("type") or Notification.Type.REGISTRATION_APPROVED
    notification = (
        Notification.objects.filter(type=notification_type)
        .select_related("user")
        .order_by("-created_at")
        .first()
    )
    if notification is None:
        notification = Notification(
            user=request.user,
            type=notification_type,
            subject="Sample: registration received",
            body=(
                "Reference JS26-B-014\n\n"
                "Registered for: JengaSec 2026\n"
                "Team: Nyati Defenders (Blue Team)\n"
                "Track preference: Application Blue, AI Defence Blue\n\n"
                "Still outstanding for your team:\n"
                "  - Upload your student ID\n"
                "  - Accept the code of conduct\n\n"
                "Organisers review registrations within 3 working days."
            ),
            link="/dashboard/",
        )

    html = email_render.render_html(notification)
    if not html:
        raise Http404("Could not render this email")
    if request.GET.get("format") == "text":
        return HttpResponse(email_render.parts(notification)[0], content_type="text/plain; charset=utf-8")
    return HttpResponse(html)


# ---------------------------------------------------------------- console


def _staff_only(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise Http404("Not found")


@login_required
def console(request):
    """Run the notification jobs from a page instead of a shell.

    Every job here is a management command, and the console calls the
    same command a shell session would -- so what an organiser clicks is
    what the scheduled runner does at 03:00, with no second
    implementation to drift. Each one previews before it sends.
    """
    _staff_only(request)

    from . import jobs
    from services import announcement_service as announce_svc

    result = None
    announcement_plan = None
    form = AnnouncementForm()

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action in ("announce_preview", "announce_send"):
            form = AnnouncementForm(request.POST)
            if form.is_valid():
                data = form.cleaned_data
                try:
                    if action == "announce_preview":
                        announcement_plan = announce_svc.preview(
                            data["side"], data["subject"], data["body"]
                        )
                    else:
                        summary = announce_svc.announce(
                            data["side"], data["subject"], data["body"],
                            pin=data["pin"], expires_at=data["expires_at"],
                        )
                        messages.success(
                            request,
                            f"Announced to {summary['reached']} recipient(s) across "
                            f"{summary['team_count']} team(s)"
                            + (" and pinned to their dashboards."
                               if summary["pinned"] else "."),
                        )
                        return redirect("notifications:console")
                except announce_svc.AnnouncementError as exc:
                    form.add_error(None, str(exc))
        else:
            job = jobs.get(request.POST.get("job", ""))
            if job is None:
                raise Http404("No such job")
            preview = action != "send"
            try:
                output = job.preview() if preview else job.run()
            except Exception as exc:  # noqa: BLE001 — shown, not raised
                output = f"{type(exc).__name__}: {exc}"
                ok = False
            else:
                ok = True
            result = {"job": job, "preview": preview, "output": output, "ok": ok}
            if ok and not preview:
                messages.success(request, f"{job.label}: done.")

    return render(
        request,
        "notifications/console.html",
        {
            "jobs": jobs.JOBS,
            "result": result,
            "form": form,
            "announcement_plan": announcement_plan,
            "pinned": Announcement.objects.filter(is_active=True),
        },
    )
