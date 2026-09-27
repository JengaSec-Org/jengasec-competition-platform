"""Registration-phase exports (Module 10).

CSV downloads of the registration data, staff-only. The generation itself
lives in services.export_service so the same rows can be produced from a
management command or a shell when someone needs them out of band.
"""
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render

from competitions.models import Competition
from services import export_service

staff_required = user_passes_test(lambda u: u.is_active and (u.is_staff or u.is_superuser))


@login_required
@staff_required
def report_index(request):
    competitions = Competition.objects.order_by("-start_date")
    return render(request, "reports/index.html", {
        "competitions": competitions,
        "exports": [(kind, label) for kind, (label, _) in export_service.EXPORTS.items()],
    })


@login_required
@staff_required
def export_csv(request, pk, kind):
    competition = get_object_or_404(Competition, pk=pk)
    entry = export_service.EXPORTS.get(kind)
    if entry is None:
        raise Http404("Unknown export")
    _label, build = entry
    # utf-8-sig: Excel on Windows needs the BOM to read accented names.
    response = HttpResponse(build(competition).encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="{export_service.filename(competition, kind)}"'
    )
    return response
