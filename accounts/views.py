from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group, User
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from services import account_service
from services import notification_catalogue as cat
from services import team_service
from notifications.management.commands.send_outstanding_digest import outstanding_for_member
from notifications.management.commands.send_registration_reminders import outstanding_for

from .forms import (
    ConductAcceptanceForm,
    InviteForm,
    ProfileForm,
    RegistrationSubmitForm,
    ResendVerificationForm,
    StaffUserCreateForm,
    StaffUserEditForm,
    TeamCreateForm,
    TeamDecisionForm,
    UserRegistrationForm,
    WelcomePackForm,
)
from .models import PolicyVersion, Team, TeamInvitation, TeamMember, UserProfile
from .roles import ALL_ROLES, TEAM_ROLES
from .security import client_ip


def _user_has_team(user):
    return team_service.team_for(user) is not None


def _profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def verified_required(view):
    """An unverified competitor account is inert (Guide section 6, step 1):
    it can sign in, but every team flow sends it back to the inbox."""

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not _profile(request.user).is_email_verified:
            return redirect("accounts:verify_pending")
        return view(request, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Sign-up and email verification
# ---------------------------------------------------------------------------

def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:main_dashboard")

    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = account_service.register_competitor(form)
            login(request, user)
            return redirect("accounts:verify_pending")
    else:
        form = UserRegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


@login_required
def verify_pending(request):
    """Where an unverified account waits. Re-sends the link on request."""
    profile = _profile(request.user)
    if profile.is_email_verified:
        return redirect("accounts:choose_role" if not _user_has_team(request.user) else "dashboard:main_dashboard")
    if request.method == "POST":
        account_service.send_verification(request.user)
        messages.success(request, f"A new verification link has been sent to {request.user.email}.")
        return redirect("accounts:verify_pending")
    return render(request, "accounts/verify_pending.html", {"profile": profile})


def verify_email(request, token):
    """The link from the email. Signs the person in on success: the token
    is proof they hold the inbox, which is what a password proves too."""
    try:
        user = account_service.confirm_verification(token, user=request.user)
    except account_service.VerificationError as exc:
        messages.error(request, str(exc))
        if request.user.is_authenticated:
            return redirect("accounts:verify_pending")
        return redirect("login")
    if not request.user.is_authenticated:
        login(request, user)
    messages.success(request, "Email verified. Your account is active.")
    if _user_has_team(user):
        return redirect("dashboard:main_dashboard")
    return redirect("accounts:choose_role")


def resend_verification(request):
    """For someone locked out with an expired link and no session."""
    if request.method == "POST":
        form = ResendVerificationForm(request.POST)
        if form.is_valid():
            user = User.objects.filter(email__iexact=form.cleaned_data["email"]).first()
            # Same answer whether or not the address exists: no enumeration.
            if user is not None and not _profile(user).is_email_verified:
                account_service.send_verification(user)
            messages.success(
                request, "If that address has an unverified account, a new link is on its way."
            )
            return redirect("login")
    else:
        form = ResendVerificationForm()
    return render(request, "accounts/resend_verification.html", {"form": form})


# ---------------------------------------------------------------------------
# Code of conduct
# ---------------------------------------------------------------------------

@login_required
def conduct(request):
    """Read and accept the current policy version (Guide section 6, step 4)."""
    version = PolicyVersion.current()
    accepted = version is not None and account_service.has_accepted_current_policy(request.user)
    form = ConductAcceptanceForm(version=version)
    if request.method == "POST" and version is not None and not accepted:
        form = ConductAcceptanceForm(request.POST, version=version)
        if form.is_valid():
            account_service.accept_policy(request.user, version, ip_address=client_ip(request) or None)
            messages.success(request, f"Code of conduct v{version.version} accepted.")
            next_url = request.POST.get("next") or reverse("accounts:team_requests")
            return redirect(next_url if next_url.startswith("/") else "accounts:team_requests")
    acceptance = (
        request.user.policy_acceptances.filter(version=version).first() if version else None
    )
    return render(
        request,
        "accounts/conduct.html",
        {"version": version, "accepted": accepted, "acceptance": acceptance, "form": form},
    )


# ---------------------------------------------------------------------------
# Teams: create, join, manage
# ---------------------------------------------------------------------------

@verified_required
def choose_role(request):
    if _user_has_team(request.user):
        return redirect("dashboard:main_dashboard")
    return render(request, "accounts/choose_role.html")


@verified_required
def team_form(request):
    if _user_has_team(request.user):
        return redirect("dashboard:main_dashboard")

    if request.method == "POST":
        form = TeamCreateForm(request.POST)
        if form.is_valid():
            try:
                team = team_service.create_team_for_captain(form, request.user)
            except team_service.TeamRuleError as exc:
                messages.error(request, str(exc))
                return redirect("dashboard:main_dashboard")
            messages.success(
                request,
                f"Team '{team.team_name}' created -- you are its captain. "
                f"Next: invite your members from My Team.",
            )
            return redirect("accounts:team_requests")
    else:
        form = TeamCreateForm()

    return render(request, "accounts/team_form.html", {"form": form})


@verified_required
def choose_team(request):
    """Team members: the invitations waiting for this account.

    Members don't pick a team -- their captain invites them (Guide
    section 6, step 6). Until then the account is in the unassigned state.
    """
    if _user_has_team(request.user):
        return redirect("accounts:team_requests")
    invitations = team_service.open_invitations_for(request.user)
    return render(request, "accounts/choose_team.html", {"invitations": invitations})


@verified_required
def invitation_respond(request, token, decision):
    """Accept or decline an invitation addressed to this account's email."""
    invitation = get_object_or_404(
        TeamInvitation.objects.select_related("team", "team__competition"), token=token
    )
    if request.method != "POST" or decision not in ("accept", "decline"):
        return redirect("accounts:choose_team")
    if decision == "decline":
        team_service.decline_invitation(invitation, request.user)
        messages.info(request, f"Invitation from {invitation.team.team_name} declined.")
        return redirect("accounts:choose_team")
    try:
        member = team_service.accept_invitation(invitation, request.user)
    except team_service.TeamRuleError as exc:
        messages.error(request, str(exc))
        return redirect("accounts:choose_team")
    cat.invitation_accepted(
        invitation.team, member, outstanding=outstanding_for_member(member)
    )
    messages.success(request, f"Welcome to {invitation.team.team_name}.")
    return redirect("dashboard:main_dashboard")


def _member_checklist(team, on_date=None):
    """[(member, [outstanding items])] for the roster, captain first."""
    members = list(team.members.select_related("user", "user__profile").order_by("-role", "student_name"))
    return [(m, outstanding_for_member(m, on_date)) for m in members]


@verified_required
def team_requests(request):
    """My Team. Captains manage it; members get the same page read-only.

    One view rather than two so the roster can never show a member less
    than the captain sees -- only the actions differ.
    """
    team = team_service.team_for(request.user)
    if not team:
        return redirect("accounts:choose_team")

    is_captain = team_service.is_captain_of(request.user, team)
    invite_form = InviteForm()
    if request.method == "POST" and is_captain:
        invite_form = InviteForm(request.POST)
        if invite_form.is_valid():
            try:
                invitation = team_service.invite(
                    team,
                    invite_form.cleaned_data["email"],
                    request.user,
                    as_reserve=invite_form.cleaned_data["as_reserve"],
                )
            except team_service.TeamRuleError as exc:
                messages.error(request, str(exc))
            else:
                cat.invitation_sent(
                    invitation.email,
                    team,
                    request.user,
                    request.build_absolute_uri(
                        reverse("accounts:invitation_open", args=[invitation.token])
                    ),
                    expires_days=TeamInvitation.EXPIRY_DAYS,
                    invitee_user=User.objects.filter(email__iexact=invitation.email).first(),
                )
                messages.success(request, f"Invitation sent to {invitation.email}. It expires in 7 days.")
            return redirect("accounts:team_requests")

    checklist = _member_checklist(team, team.submitted_at.date() if team.submitted_at else None)
    invitations = (
        team.invitations.filter(status=TeamInvitation.Status.PENDING).order_by("-created_at")
        if is_captain
        else TeamInvitation.objects.none()
    )
    team_outstanding = outstanding_for(team)
    member_outstanding = sum(len(items) for _, items in checklist)
    my_items = next((items for m, items in checklist if m.user_id == request.user.pk), [])
    return render(
        request,
        "accounts/team_requests.html",
        {
            "team": team,
            "checklist": checklist,
            "members": [m for m, _ in checklist],
            "invitations": invitations,
            "invite_form": invite_form,
            "is_captain": is_captain,
            "outstanding": team_outstanding,
            "my_items": my_items,
            "ready_to_submit": team.can_submit and not team_outstanding and not member_outstanding,
            "playing_count": team_service.playing_count(team),
            "min_members": team_service.MIN_MEMBERS,
            "max_members": team_service.MAX_MEMBERS,
            "policy": PolicyVersion.current(),
        },
    )


@verified_required
def registration_submit(request):
    """Captain's final step (Guide section 6, step 9): preferences, skills
    declaration, mentor -- then the registration locks for review."""
    team = team_service.team_for(request.user)
    if not team or not team_service.is_captain_of(request.user, team):
        return redirect("accounts:team_requests")
    if not team.can_submit:
        messages.info(request, f"This registration is {team.get_status_display().lower()}.")
        return redirect("accounts:team_requests")

    blockers = team_service.registration_blockers(team)
    if request.method == "POST":
        form = RegistrationSubmitForm(request.POST, team=team)
        if form.is_valid():
            try:
                team_service.submit_registration(
                    team,
                    track_preferences=form.track_preferences,
                    enterprise_preference=form.cleaned_data["enterprise_preference"],
                    skills_declaration=form.cleaned_data["skills_declaration"],
                    mentor=form.mentor,
                )
            except team_service.TeamRuleError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Registration submitted. Reference {cat.registration_reference(team)}; "
                    f"organisers review within {cat.REVIEW_WINDOW}.",
                )
                return redirect("accounts:team_requests")
    else:
        form = RegistrationSubmitForm(team=team)

    return render(
        request,
        "accounts/registration_submit.html",
        {"team": team, "form": form, "blockers": blockers},
    )


@login_required
def invitation_open(request, token):
    """The link in the invitation email. Shows the invitation to whoever opens it."""
    invitation = get_object_or_404(TeamInvitation.objects.select_related("team"), token=token)
    return render(
        request,
        "accounts/invitation.html",
        {"invitation": invitation, "verified": _profile(request.user).is_email_verified},
    )


@login_required
def invitation_revoke(request, pk):
    team = Team.objects.filter(captain=request.user).first()
    invitation = get_object_or_404(TeamInvitation, pk=pk, team=team)
    if request.method == "POST" and invitation.status == TeamInvitation.Status.PENDING:
        invitation.status = TeamInvitation.Status.REVOKED
        invitation.decided_at = timezone.now()
        invitation.save(update_fields=["status", "decided_at"])
        messages.info(request, f"Invitation to {invitation.email} withdrawn.")
    return redirect("accounts:team_requests")


@login_required
def member_toggle_specialist(request, member_id):
    team = Team.objects.filter(captain=request.user).first()
    member = get_object_or_404(TeamMember, id=member_id, team=team)
    if request.method == "POST":
        try:
            team_service.check_unlocked(team, "change the security specialist")
        except team_service.TeamRuleError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:team_requests")
        member.is_security_specialist = not member.is_security_specialist
        member.save(update_fields=["is_security_specialist"])
    return redirect("accounts:team_requests")


@login_required
def member_toggle_deputy(request, member_id):
    team = Team.objects.filter(captain=request.user).first()
    member = get_object_or_404(TeamMember, id=member_id, team=team)
    if request.method == "POST":
        try:
            deputy = team_service.set_deputy(team, member)
        except team_service.TeamRuleError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"{member.student_name} is now the deputy." if deputy else f"{member.student_name} is no longer the deputy.",
            )
    return redirect("accounts:team_requests")


@login_required
def team_member_remove(request, member_id):
    team = Team.objects.filter(captain=request.user).first()
    if not team:
        return redirect("dashboard:main_dashboard")

    member = get_object_or_404(TeamMember, id=member_id, team=team)
    if request.method == "POST":
        student_name = member.student_name
        try:
            # notifications/signals.on_member_deleted tells both the captain
            # and the member, with the grace period -- nothing to send here.
            team_service.remove_member(team, member)
        except team_service.TeamRuleError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:team_requests")
        messages.success(request, f"{student_name} was removed from the team.")

    return redirect("accounts:team_requests")


# ---------------------------------------------------------------------------
# Own profile
# ---------------------------------------------------------------------------

@login_required
def profile(request):
    profile_obj = _profile(request.user)
    member = TeamMember.objects.filter(user=request.user).select_related("team").first()
    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=profile_obj)
        if form.is_valid():
            form.save()
            if form.email_changed:
                account_service.send_verification(request.user)
                messages.warning(
                    request,
                    "Your email address changed, so it has to be verified again -- "
                    "a link has been sent to the new address.",
                )
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=profile_obj)
    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "profile": profile_obj,
            "team": team_service.team_for(request.user),
            "outstanding": outstanding_for_member(member) if member else [],
            "policy_accepted": account_service.has_accepted_current_policy(request.user),
            "policy": PolicyVersion.current(),
        },
    )


# ---------------------------------------------------------------------------
# User management (staff only)
# ---------------------------------------------------------------------------

def _is_staff(user):
    return user.is_active and (user.is_staff or user.is_superuser)


staff_required = user_passes_test(_is_staff)

ROLE_DESCRIPTIONS = {
    "admin": "Organisers. Identified by is_staff / is_superuser rather than a group.",
    "blue_team": "Builds and defends a system. Blue Team dashboard, submissions.",
    "red_team": "Attacks assigned targets. Red Team dashboard, target access.",
    "judge": "Scores submissions against rubrics. Insights dashboard, review queue.",
    "partner": "Read-only view of competition insights.",
    "captain": "Derived: captain of at least one team. Manages roster and join requests.",
    "team_member": "Derived: on at least one team. Read-only roster view.",
}


@login_required
@staff_required
def user_list(request):
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    users = User.objects.select_related("profile").order_by("username")
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )
    if role:
        users = users.filter(profile__role=role)
    if request.GET.get("manual"):
        users = users.filter(
            profile__manual_verification_required=True, profile__manual_verified_at__isnull=True
        )

    stats = {
        "total": User.objects.count(),
        "active": User.objects.filter(is_active=True).count(),
        "judges": UserProfile.objects.filter(role=UserProfile.Role.JUDGE).count(),
        "teams": Team.objects.count(),
        "manual": UserProfile.objects.filter(
            manual_verification_required=True, manual_verified_at__isnull=True
        ).count(),
    }
    return render(
        request,
        "accounts/users.html",
        {"users": users, "q": q, "role": role, "roles": UserProfile.Role.choices, "stats": stats},
    )


@login_required
@staff_required
def user_create(request):
    if request.method == "POST":
        form = StaffUserCreateForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            user = account_service.issue_account(
                username=d["username"],
                email=d["email"],
                first_name=d["first_name"],
                last_name=d["last_name"],
                role=d["role"],
                institution=d.get("institution", ""),
                issued_by=request.user,
            )
            messages.success(
                request,
                f"Account for {user.username} created. "
                f"A set-password link has been emailed to {user.email}.",
            )
            return redirect("accounts:user_list")
    else:
        form = StaffUserCreateForm()
    return render(request, "accounts/user_form.html", {"form": form, "mode": "create"})


@login_required
@staff_required
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    profile_obj, _ = UserProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        if "resend_link" in request.POST:
            account_service.resend_set_password_link(user)
            messages.success(request, f"Set-password link re-sent to {user.email}.")
            return redirect("accounts:user_edit", pk=pk)
        if "verify_institution" in request.POST:
            account_service.mark_institution_verified(profile_obj, request.user)
            messages.success(request, f"{user.username}'s student status marked as verified.")
            return redirect("accounts:user_edit", pk=pk)
        if "verify_email" in request.POST:
            if profile_obj.email_verified_at is None:
                profile_obj.email_verified_at = timezone.now()
                profile_obj.save(update_fields=["email_verified_at"])
            messages.success(request, f"{user.email} marked as verified.")
            return redirect("accounts:user_edit", pk=pk)
        form = StaffUserEditForm(request.POST, instance=user)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = form.cleaned_data["is_active"]
            user.save()
            profile_obj.role = form.cleaned_data["role"]
            profile_obj.institution = form.cleaned_data.get("institution", "")
            profile_obj.save()
            messages.success(request, f"{user.username} updated.")
            return redirect("accounts:user_list")
    else:
        form = StaffUserEditForm(
            instance=user,
            initial={
                "role": profile_obj.role,
                "institution": profile_obj.institution,
                "is_active": user.is_active,
            },
        )
    return render(
        request,
        "accounts/user_form.html",
        {
            "form": form,
            "mode": "edit",
            "edited": user,
            "profile": profile_obj,
            "needs_password": not user.has_usable_password(),
        },
    )


@login_required
@staff_required
def identity_document(request, pk):
    """Stream a competitor's ID to an organiser. Never linked publicly."""
    profile_obj = get_object_or_404(UserProfile, user__pk=pk)
    if not profile_obj.identity_document:
        raise Http404
    return FileResponse(
        profile_obj.identity_document.open("rb"),
        as_attachment=True,
        filename=profile_obj.identity_document.name.rsplit("/", 1)[-1],
    )


@login_required
@staff_required
def role_list(request):
    groups = {g.name: g for g in Group.objects.filter(name__in=ALL_ROLES + TEAM_ROLES)}
    rows = [
        {
            "name": "admin",
            "label": "Admin",
            "count": User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).count(),
            "derived": False,
            "description": ROLE_DESCRIPTIONS["admin"],
        }
    ]
    for name in ALL_ROLES + TEAM_ROLES:
        g = groups.get(name)
        rows.append(
            {
                "name": name,
                "label": name.replace("_", " ").title(),
                "count": g.user_set.count() if g else 0,
                "derived": name in TEAM_ROLES,
                "description": ROLE_DESCRIPTIONS.get(name, ""),
            }
        )
    return render(request, "accounts/roles.html", {"roles": rows})


@login_required
@staff_required
def team_list(request):
    status = request.GET.get("status", "")
    teams = (
        Team.objects.select_related("competition", "captain")
        .annotate(
            member_count=Count("members", distinct=True),
            pending_count=Count(
                "invitations",
                filter=Q(invitations__status=TeamInvitation.Status.PENDING),
                distinct=True,
            ),
        )
        .order_by("competition__name", "team_name")
    )
    if status:
        teams = teams.filter(status=status)
    counts = {s: Team.objects.filter(status=s).count() for s, _ in Team.Status.choices}
    return render(
        request,
        "accounts/teams.html",
        {"teams": teams, "status": status, "statuses": Team.Status.choices, "counts": counts},
    )


def _team_detail_context(team, form, pack_form=None):
    return {
        "team": team,
        "checklist": _member_checklist(team, team.submitted_at.date() if team.submitted_at else None),
        "invitations": team.invitations.filter(status=TeamInvitation.Status.PENDING),
        "form": form,
        "pack_form": pack_form or WelcomePackForm(instance=team),
        "reference": cat.registration_reference(team),
        "outstanding": outstanding_for(team),
        "suggested": team_service.suggested_cell(team),
    }


@login_required
@staff_required
def team_detail(request, pk):
    team = get_object_or_404(Team.objects.select_related("competition", "captain"), pk=pk)
    suggested_ent, suggested_cell = team_service.suggested_cell(team)
    form = TeamDecisionForm(
        initial={
            "cell_id": team.cell_id or (suggested_cell if not team.submits_proposal else ""),
            "enterprise": team.enterprise or suggested_ent,
            "rejection_reason": "",
        }
    )
    return render(request, "accounts/team_details.html", _team_detail_context(team, form))


@login_required
@staff_required
def team_decide(request, pk):
    """Organiser outcome for a registration. Fires Module 12's notifications."""
    team = get_object_or_404(Team, pk=pk)
    if request.method != "POST":
        return redirect("accounts:team_detail", pk=pk)
    form = TeamDecisionForm(request.POST)
    if not form.is_valid():
        return render(request, "accounts/team_details.html", _team_detail_context(team, form))

    d = form.cleaned_data
    if d["decision"] == Team.Status.APPROVED:
        team_service.assign_identifier(team)
        try:
            if team.application_brief_id or d.get("enterprise"):
                team.enterprise = d.get("enterprise") or team.application_brief.enterprise
            # Only Application Red is placed at registration; everyone else
            # gets a cell when their proposal is selected (Guide section 4).
            if not team.submits_proposal or d.get("cell_id"):
                team_service.assign_cell(team, cell_id=d.get("cell_id", ""), enterprise=d.get("enterprise", ""))
        except team_service.TeamRuleError as exc:
            form.add_error("cell_id", str(exc))
            return render(request, "accounts/team_details.html", _team_detail_context(team, form))
        team.status = Team.Status.APPROVED
        team.rejection_reason = ""
        team.save()  # notifications/signals -> registration_approved
        placed = f" Cell {team.cell_label}." if team.cell_id else " A cell is assigned at proposal selection."
        messages.success(request, f"{team.team_name} approved as {team.team_identifier}.{placed}")
    else:
        team.status = Team.Status.REJECTED
        team.rejection_reason = d["rejection_reason"].strip()
        team.save()  # notifications/signals -> registration_rejected
        messages.success(request, f"{team.team_name} rejected; the captain has been told why.")
    return redirect("accounts:team_detail", pk=pk)


@login_required
@staff_required
def team_welcome_pack(request, pk):
    """Organisers record the repository and namespace issued to a team."""
    team = get_object_or_404(Team, pk=pk)
    if request.method != "POST":
        return redirect("accounts:team_detail", pk=pk)
    pack_form = WelcomePackForm(request.POST, instance=team)
    if not pack_form.is_valid():
        form = TeamDecisionForm(initial={"cell_id": team.cell_id, "enterprise": team.enterprise})
        return render(
            request, "accounts/team_details.html", _team_detail_context(team, form, pack_form)
        )
    pack_form.save()
    messages.success(request, f"Welcome pack for {team.team_name} saved.")
    return redirect("accounts:team_detail", pk=pk)
