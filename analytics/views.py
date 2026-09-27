"""Registration analytics for organisers (Module 11).

One page answering "how is turnout going, and what is blocking it".
Staff-only: it aggregates personal data (verification state, eligibility)
across the whole field.
"""
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from competitions.models import Competition
from services import analytics_service

staff_required = user_passes_test(lambda u: u.is_active and (u.is_staff or u.is_superuser))


@login_required
@staff_required
def registration_analytics(request, pk=None):
    competitions = Competition.objects.order_by("-start_date")
    if not competitions.exists():
        raise Http404("No competitions yet")
    competition = (
        get_object_or_404(Competition, pk=pk) if pk
        else competitions.filter(
            status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS)
        ).first() or competitions.first()
    )
    context = analytics_service.overview(competition)
    context["competitions"] = competitions
    return render(request, "analytics/registration.html", context)
