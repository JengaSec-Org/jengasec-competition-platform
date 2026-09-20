"""Audit log browser (Command Center)."""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils.dateparse import parse_date

from .models import AuditLog


def _require_staff(user):
    if not (user.is_active and (user.is_staff or user.is_superuser)):
        raise Http404("Not found")


@login_required
def audit_log(request):
    """Filterable, read-only view of the platform audit trail."""
    _require_staff(request.user)

    entries = AuditLog.objects.select_related("actor")

    category = request.GET.get("category", "")
    action = request.GET.get("action", "")
    actor = request.GET.get("actor", "")
    since = parse_date(request.GET.get("since", "") or "")
    until = parse_date(request.GET.get("until", "") or "")
    query = (request.GET.get("q", "") or "").strip()

    if category in AuditLog.Category.values:
        entries = entries.filter(category=category)
    if action in AuditLog.Action.values:
        entries = entries.filter(action=action)
    if actor:
        entries = entries.filter(actor_label__iexact=actor)
    if since:
        entries = entries.filter(created_at__date__gte=since)
    if until:
        entries = entries.filter(created_at__date__lte=until)
    if query:
        entries = entries.filter(
            Q(description__icontains=query)
            | Q(target_label__icontains=query)
            | Q(ip_address__icontains=query)
        )

    page = Paginator(entries, 50).get_page(request.GET.get("page"))

    # Preserve filters across pagination links.
    params = request.GET.copy()
    params.pop("page", None)

    return render(
        request,
        "audit/audit_log.html",
        {
            "entries": page,
            "page_obj": page,
            "categories": AuditLog.Category.choices,
            "actions": AuditLog.Action.choices,
            "actors": User.objects.order_by("username").values_list(
                "username", flat=True
            ),
            "filters": {
                "category": category,
                "action": action,
                "actor": actor,
                "since": request.GET.get("since", ""),
                "until": request.GET.get("until", ""),
                "q": query,
            },
            "querystring": params.urlencode(),
            "total": entries.count(),
        },
    )
