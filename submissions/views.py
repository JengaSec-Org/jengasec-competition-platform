"""Submission Management views (Module 4).

The upload wizard is three steps:
    1. Select competition + document type (time-gated)
    2. Upload the document            (saved immediately as a draft version)
    3. Review and submit              (finalizes + notifies)

Business rules live in services/submission_service.py, not here.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from services import competition_service as comp_svc
from services import submission_service as svc

from accounts.roles import JUDGE, user_in_role
from audit.models import AuditLog
from competitions.models import ApplicationBrief, Competition
from services.audit_service import record as audit

from .forms import MAX_UPLOAD_BYTES, SubmissionFileForm
from .models import DocumentImage, Submission, SubmissionFile, SubmissionType

WIZARD_SESSION_KEY = "submission_wizard"


def _safe_int(value):
    """Coerce user input to int, or None. Never raises.

    Hostile query strings (?brief=abc) must not reach int()/filter() and
    blow up into a 500 — which leaks a stack trace when DEBUG is on.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _oversized(request):
    """True when the client announced a body bigger than we accept.

    Checked before touching request.FILES: Django spools the whole upload
    to a temp file first, so without this a huge POST fills the disk
    before the form's size check ever runs.
    """
    try:
        declared = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return False
    # Allow a little headroom for the multipart envelope itself.
    return declared > MAX_UPLOAD_BYTES + (1024 * 1024)


def _wizard_state(request):
    return request.session.get(WIZARD_SESSION_KEY) or {}


def _clear_wizard(request):
    request.session.pop(WIZARD_SESSION_KEY, None)


@login_required
def submission_list(request):
    """All submissions for staff; a team's own for everyone else."""
    queryset = svc.visible_submissions(request.user)
    page = Paginator(queryset, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "submissions/submission_list.html",
        {"submissions": page, "page_obj": page, "can_start": svc.open_competitions_for(request.user).exists()},
    )


@login_required
def submission_detail(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("team", "competition", "submission_type"), pk=pk
    )
    if not svc.can_view(request.user, submission):
        raise Http404("Submission not found")
    latest = submission.latest_file
    parsed = getattr(latest, "parsed", None) if latest else None
    return render(
        request,
        "submissions/submission_detail.html",
        {
            "submission": submission,
            "files": submission.files.select_related("uploaded_by", "parsed"),
            "can_upload": svc.can_upload(request.user, submission),
            "parsed": parsed,
            "figures": parsed.images.all() if parsed else [],
            "tables": parsed.tables if parsed else [],
            "can_change_marking": request.user.is_staff
            or user_in_role(request.user, JUDGE),
        },
    )


@login_required
def download_file(request, pk):
    """Serve an uploaded document through a permission check.

    Deliberately not a raw /media/ link — submission documents must not be
    publicly reachable by guessing a URL.
    """
    submission_file = get_object_or_404(
        SubmissionFile.objects.select_related("submission"), pk=pk
    )
    if not svc.can_view(request.user, submission_file.submission):
        raise Http404("File not found")
    audit(
        AuditLog.Action.FILE_DOWNLOADED,
        request=request,
        target=submission_file.submission,
        description=f"Downloaded {submission_file.filename} (v{submission_file.version})",
        file_id=submission_file.pk,
    )
    return FileResponse(
        submission_file.file.open("rb"),
        as_attachment=True,
        filename=submission_file.filename,
    )


# ---------------------------------------------------------------- wizard


@login_required
def wizard_step1(request):
    """Step 1 — choose the competition and document type."""
    competitions = svc.open_competitions_for(request.user)
    teams = svc.teams_for(request.user)

    if not competitions.exists():
        return render(
            request,
            "submissions/wizard_closed.html",
            {"teams": teams},
        )

    # Only the user's own teams can be chosen, keyed by their competition.
    team_by_competition = {t.competition_id: t for t in teams}
    selected_competition_id = _safe_int(
        request.POST.get("competition") or request.GET.get("competition")
    )
    team = team_by_competition.get(selected_competition_id)
    types = svc.submission_types_for(team) if team else SubmissionType.objects.none()

    # Arriving from the application catalogue: ?brief=<id> preselects the
    # competition and carries the chosen application into the proposal.
    # The brief is only honoured for the requesting team's own competition —
    # otherwise a crafted id would attach another competition's application.
    brief = None
    brief_id = _safe_int(request.POST.get("brief") or request.GET.get("brief"))
    if brief_id is not None:
        brief = _resolve_brief(brief_id, team_by_competition)
        if brief and selected_competition_id is None:
            selected_competition_id = brief.competition_id
            team = team_by_competition.get(brief.competition_id)
            types = svc.submission_types_for(team) if team else types

    if request.method == "POST" and request.POST.get("action") == "next":
        type_id = _safe_int(request.POST.get("submission_type"))
        if team is None or type_id is None:
            messages.error(request, "Choose a competition and a document type.")
        elif not types.filter(pk=type_id).exists():
            # The type must be one this team is actually allowed to file —
            # never trust the posted id, the <select> is client-side only.
            messages.error(request, "Your team cannot submit that type of document.")
        elif brief is not None and not comp_svc.can_propose(team, brief):
            messages.error(
                request, "That application is no longer accepting proposals."
            )
        else:
            request.session[WIZARD_SESSION_KEY] = {
                "competition_id": team.competition_id,
                "team_id": team.pk,
                "type_id": type_id,
                "brief_id": brief.pk if brief else None,
            }
            return redirect("submissions:wizard_step2")

    return render(
        request,
        "submissions/wizard_step1.html",
        {
            "competitions": competitions,
            "teams": teams,
            "types": types,
            "selected_competition_id": selected_competition_id,
            "brief": brief,
        },
    )


def _resolve_brief(brief_id, team_by_competition):
    """An application brief the requesting team is entitled to propose against.

    Scoped to the team's own competition and enterprise, so a guessed or
    borrowed id cannot pull in another competition's application.
    """
    brief = ApplicationBrief.objects.filter(pk=brief_id, is_open=True).first()
    if brief is None:
        return None
    team = team_by_competition.get(brief.competition_id)
    if team is None:
        return None
    if brief.enterprise and team.enterprise and brief.enterprise != team.enterprise:
        return None
    return brief


@login_required
def wizard_step2(request):
    """Step 2 — upload the document (saved right away as a draft version)."""
    state = _wizard_state(request)
    if not state:
        return redirect("submissions:wizard_step1")

    team = svc.teams_for(request.user).filter(pk=state["team_id"]).first()
    if team is None:
        _clear_wizard(request)
        return redirect("submissions:wizard_step1")

    competition = svc.open_competitions_for(request.user).filter(
        pk=state["competition_id"]
    ).first()
    if competition is None:
        _clear_wizard(request)
        messages.error(request, "That competition is no longer accepting submissions.")
        return redirect("submissions:wizard_step1")

    # Re-check the document type here too: session state is replayable, so
    # step 1's validation alone is not a control.
    submission_type = (
        svc.submission_types_for(team).filter(pk=_safe_int(state.get("type_id"))).first()
    )
    if submission_type is None:
        _clear_wizard(request)
        messages.error(request, "Your team cannot submit that type of document.")
        return redirect("submissions:wizard_step1")

    submission = svc.get_or_create_thread(team, competition, submission_type)

    # Record which lined-up application a proposal targets, re-validating
    # ownership of the brief rather than trusting the stored id.
    brief_id = _safe_int(state.get("brief_id"))
    if brief_id is not None and submission.application_brief_id is None:
        brief = ApplicationBrief.objects.filter(
            pk=brief_id, is_open=True, competition=competition
        ).first()
        if brief is not None:
            submission.application_brief = brief
            submission.save(update_fields=["application_brief", "updated_at"])

    if request.method == "POST" and _oversized(request):
        messages.error(request, "That file is larger than the 50 MB limit.")
        return redirect("submissions:wizard_step2")

    form = SubmissionFileForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not svc.can_upload(request.user, submission):
            messages.error(request, "You cannot upload to this submission.")
            return redirect("submissions:list")
        if form.is_valid():
            try:
                uploaded = svc.add_version(submission, form, request.user)
            except ValueError as exc:
                # Business rules (e.g. the version cap) surface as a message,
                # never as a 500.
                messages.error(request, str(exc))
                return redirect("submissions:detail", pk=submission.pk)
            svc.parse_file(uploaded)
            state["submission_id"] = submission.pk
            state["file_id"] = uploaded.pk
            request.session[WIZARD_SESSION_KEY] = state
            return redirect("submissions:wizard_step3")

    return render(
        request,
        "submissions/wizard_step2.html",
        {
            "form": form,
            "submission": submission,
            "team": team,
            "competition": competition,
            "submission_type": submission_type,
        },
    )


@login_required
def wizard_step3(request):
    """Step 3 — review the upload, then submit."""
    state = _wizard_state(request)
    if not state.get("file_id"):
        return redirect("submissions:wizard_step1")

    submission_file = get_object_or_404(
        SubmissionFile.objects.select_related("submission"), pk=state["file_id"]
    )
    submission = submission_file.submission
    if not svc.can_view(request.user, submission):
        raise Http404("Submission not found")

    if request.method == "POST":
        if not svc.can_upload(request.user, submission):
            messages.error(request, "This submission can no longer be changed.")
            return redirect("submissions:list")
        svc.finalize(submission, request.user)
        _clear_wizard(request)
        messages.success(
            request,
            f"{submission.submission_type.name} submitted "
            f"(version {submission.current_version}).",
        )
        return redirect("submissions:detail", pk=submission.pk)

    return render(
        request,
        "submissions/wizard_step3.html",
        {
            "submission": submission,
            "submission_file": submission_file,
            "parsed": getattr(submission_file, "parsed", None),
        },
    )


@login_required
def upload_new_version(request, pk):
    """Re-upload onto an existing thread — becomes v2, v3, …"""
    submission = get_object_or_404(Submission, pk=pk)
    if not svc.can_upload(request.user, submission):
        raise Http404("Submission not found")

    if request.method == "POST" and _oversized(request):
        messages.error(request, "That file is larger than the 50 MB limit.")
        return redirect("submissions:new_version", pk=submission.pk)

    form = SubmissionFileForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            uploaded = svc.add_version(submission, form, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("submissions:detail", pk=submission.pk)
        svc.parse_file(uploaded)
        request.session[WIZARD_SESSION_KEY] = {
            "competition_id": submission.competition_id,
            "team_id": submission.team_id,
            "type_id": submission.submission_type_id,
            "submission_id": submission.pk,
            "file_id": uploaded.pk,
        }
        return redirect("submissions:wizard_step3")

    return render(
        request,
        "submissions/wizard_step2.html",
        {
            "form": form,
            "submission": submission,
            "team": submission.team,
            "competition": submission.competition,
            "submission_type": submission.submission_type,
            "is_new_version": True,
        },
    )


@login_required
def figure_image(request, pk):
    """Serve one extracted figure through the same ownership check.

    Figures come out of a team's document, so they inherit exactly the
    document's access rules — never a raw /media/ URL.
    """
    figure = get_object_or_404(
        DocumentImage.objects.select_related(
            "parsed_document__submission_file__submission"
        ),
        pk=pk,
    )
    submission = figure.parsed_document.submission_file.submission
    if not svc.can_view(request.user, submission):
        raise Http404("Figure not found")
    return FileResponse(figure.image.open("rb"))


# ------------------------------------------------------- organiser screens


def _require_staff(user):
    """404 rather than 403 so the routes stay invisible to participants."""
    if not (user.is_active and (user.is_staff or user.is_superuser)):
        raise Http404("Not found")


SELECTION_ACTIONS = {
    "shortlist": Submission.SelectionStatus.SHORTLISTED,
    "select": Submission.SelectionStatus.SELECTED,
    "reject": Submission.SelectionStatus.REJECTED,
    "reset": Submission.SelectionStatus.PENDING,
}


@login_required
def proposal_review(request, competition_id=None):
    """Review proposals per application and record the outcome.

    Registration is deliberately many-teams-per-application, so this is
    where organisers work through the field and pick who builds what.
    Scoring itself stays with the judging modules.
    """
    _require_staff(request.user)

    if request.method == "POST":
        proposal = get_object_or_404(
            Submission.objects.select_related("team", "application_brief"),
            pk=_safe_int(request.POST.get("proposal")),
        )
        action = request.POST.get("action", "")
        if action not in SELECTION_ACTIONS:
            messages.error(request, "Unknown action.")
        else:
            previous = proposal.get_selection_status_display()
            proposal.selection_status = SELECTION_ACTIONS[action]
            proposal.save(update_fields=["selection_status", "updated_at"])
            audit(
                AuditLog.Action.PROPOSAL_SELECTION,
                request=request,
                target=proposal,
                description=(
                    f"{proposal.team.team_name}: {previous} -> "
                    f"{proposal.get_selection_status_display()}"
                ),
                brief=getattr(proposal.application_brief, "code", ""),
            )
            messages.success(
                request,
                f"{proposal.team.team_name} marked "
                f"{proposal.get_selection_status_display().lower()}.",
            )
        return redirect(request.path)

    proposals = (
        Submission.objects.filter(submission_type__name="Proposal")
        .select_related("team", "competition", "application_brief")
        .order_by("application_brief__code", "team__team_name")
    )
    competition_id = _safe_int(competition_id or request.GET.get("competition"))
    if competition_id:
        proposals = proposals.filter(competition_id=competition_id)

    # Group by application so organisers compare like with like.
    grouped = {}
    for proposal in proposals:
        brief = proposal.application_brief
        key = brief.code if brief else "Unassigned"
        grouped.setdefault(key, {"brief": brief, "proposals": []})
        grouped[key]["proposals"].append(proposal)

    return render(
        request,
        "submissions/proposal_review.html",
        {
            "groups": sorted(grouped.items()),
            "competitions": Competition.objects.all(),
            "selected_competition": competition_id,
        },
    )


@login_required
def toggle_marking(request, pk):
    """Staff or a judge decides whether a late submission gets marked."""
    submission = get_object_or_404(Submission, pk=pk)
    user = request.user
    allowed = user.is_staff or user_in_role(user, JUDGE)
    if not allowed:
        raise Http404("Not found")
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    submission.marking_excluded = not submission.marking_excluded
    submission.marking_note = (request.POST.get("note") or "")[:300]
    submission.save(
        update_fields=["marking_excluded", "marking_note", "updated_at"]
    )
    audit(
        AuditLog.Action.MARKING_CHANGED,
        request=request,
        target=submission,
        description=(
            "Excluded from marking"
            if submission.marking_excluded
            else "Restored for marking"
        ),
        note=submission.marking_note,
    )
    messages.success(
        request,
        "Submission excluded from marking."
        if submission.marking_excluded
        else "Submission restored for marking.",
    )
    return redirect("submissions:detail", pk=submission.pk)
