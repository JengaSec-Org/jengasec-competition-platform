"""Internal independent appeal review; teams read decisions on their result."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from accounts.roles import JUDGE, user_in_role
from services import appeal_service
from .forms import AppealResolutionForm
from .models import Appeal


def _organizer(user):
    return user.is_active and (user.is_staff or user.is_superuser)


def _internal(user):
    return user.is_active and (_organizer(user) or user_in_role(user, JUDGE))


def _query():
    return Appeal.objects.select_related(
        "team", "submission__competition", "submission__submission_type",
        "score_revision__event__team", "score_revision__event__competition",
        "second_judge", "resolved_by",
    )


def _bounded(request):
    try:
        size = int(request.META.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        return False
    return 0 <= size <= 65536


@login_required
def appeal_list(request):
    if not _internal(request.user):
        raise Http404("Not found")
    appeals = _query()
    if not _organizer(request.user):
        appeals = appeals.filter(second_judge=request.user)
    show_all = request.GET.get("status") == "all"
    if not show_all:
        appeals = appeals.filter(status__in=appeal_service.ACTIVE_STATUSES)
    page = Paginator(appeals, 50).get_page(request.GET.get("page"))
    return render(request, "judging/appeals.html", {"page_obj": page, "show_all": show_all})


@login_required
def appeal_detail(request, pk):
    if not _internal(request.user):
        raise Http404("Not found")
    appeal = get_object_or_404(_query(), pk=pk)
    if not (_organizer(request.user) or appeal.second_judge_id == request.user.pk):
        raise Http404("Not found")
    form = AppealResolutionForm()
    if request.method == "POST":
        if not _bounded(request):
            return HttpResponse("Appeal request is too large.", status=413)
        action = request.POST.get("action")
        if action == "assign":
            if not _organizer(request.user):
                raise Http404("Not found")
            try:
                identifier = int(request.POST.get("judge", ""))
            except (TypeError, ValueError):
                messages.error(request, "Choose an independent judge.")
                return redirect("judging:appeal_detail", pk=appeal.pk)
            judge = get_object_or_404(get_user_model(), pk=identifier)
            try:
                appeal_service.assign_second_judge(appeal, judge, by=request.user)
            except appeal_service.AppealError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Independent reviewer assigned.")
            return redirect("judging:appeal_detail", pk=appeal.pk)
        if action != "resolve":
            return HttpResponse("Unknown appeal action.", status=400)
        form = AppealResolutionForm(request.POST)
        if form.is_valid():
            try:
                appeal_service.resolve_appeal(appeal, request.user,
                                             form.cleaned_data["status"], form.cleaned_data["resolution"])
            except appeal_service.AppealError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Appeal decision recorded. Any score correction is a separate reviewed action.")
                return redirect("judging:appeal_detail", pk=appeal.pk)
    elif request.method != "GET":
        return HttpResponseNotAllowed(["GET", "POST"])
    active = appeal.status in appeal_service.ACTIVE_STATUSES
    return render(request, "judging/appeal_detail.html", {
        "appeal": appeal, "resolution_form": form,
        "can_assign": _organizer(request.user) and active,
        "second_judges": appeal_service.eligible_second_judges(appeal) if _organizer(request.user) else [],
        "can_resolve": active and appeal.second_judge_id == request.user.pk and
                       appeal_service.eligible_second_judges(appeal).filter(pk=request.user.pk).exists(),
        "archives": appeal.retained_evidence.all(),
    }, status=400 if request.method == "POST" else 200)


@login_required
def withdraw_appeal(request, pk):
    appeal = get_object_or_404(_query(), pk=pk, team__captain=request.user)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not _bounded(request):
        return HttpResponse("Appeal request is too large.", status=413)
    try:
        appeal_service.withdraw_appeal(appeal, request.user, request.POST.get("reason", ""))
    except appeal_service.AppealError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Appeal withdrawn.")
    if appeal.score_revision_id:
        return redirect("scoring:revision", pk=appeal.score_revision_id)
    return redirect("submissions:detail", pk=appeal.submission_id)
