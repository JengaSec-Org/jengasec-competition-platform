from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import Group, User
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from services import account_service
from competitions.models import Competition
from services import competition_service
from services import deployment_service
from services import matching_service
from services import notification_catalogue as cat
from services import stored_files
from services import team_service
from notifications.management.commands.send_outstanding_digest import outstanding_for_member
from notifications.management.commands.send_outstanding_digest import outstanding_for_user
from notifications.management.commands.send_registration_reminders import outstanding_for

from .forms import (
    ParticipantTermsForm,
    TeamEditForm,
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
from .models import (
    ParticipantConsent, PolicyVersion, Team, TeamInvitation, TeamMember, UserProfile,
)
from .roles import ALL_ROLES, TEAM_ROLES
from . import storage_diagnostics
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
            user = account_service.register_competitor(form, ip_address=client_ip(request))
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


@login_required
def terms(request):
    """Read the Participant Terms, agree, and make the optional choices.

    The agreement is one tick recorded as a `PolicyAcceptance`, like any
    other policy. The optional choices live on `ParticipantConsent` and can
    be changed afterwards without re-agreeing -- clause 7.1 says a change
    never reduces a choice already made, so coming back to tick one more
    box must not look like starting again.
    """
    version = PolicyVersion.current(PolicyVersion.Kind.TERMS)
    accepted = version is not None and account_service.has_accepted_current_policy(
        request.user, PolicyVersion.Kind.TERMS
    )
    consent = ParticipantConsent.objects.filter(user=request.user).first()

    form = ParticipantTermsForm(
        instance=consent, version=version, already_accepted=accepted
    )
    if request.method == "POST" and version is not None:
        form = ParticipantTermsForm(
            request.POST, instance=consent, version=version,
            already_accepted=accepted,
        )
        if form.is_valid():
            record = form.save(commit=False)
            record.user = request.user
            record.version = version
            record.save()
            if not accepted:
                account_service.accept_policy(
                    request.user, version, ip_address=client_ip(request) or None
                )
                messages.success(
                    request,
                    f"Terms v{version.version} accepted. Your choices are saved — "
                    f"you can change them here at any time.",
                )
            else:
                messages.success(request, "Your choices have been updated.")
            next_url = request.POST.get("next") or ""
            if next_url.startswith("/"):
                return redirect(next_url)
            return redirect("accounts:terms")

    acceptance = (
        request.user.policy_acceptances.filter(version=version).first()
        if version else None
    )
    def group(rows):
        """Bound fields with their published code and wording.

        Built here because a template cannot look a form field up by a
        name it has to read out of a list first.
        """
        return [
            {"field": form[name], "code": code, "label": label}
            for name, code, label in rows
        ]

    return render(request, "accounts/terms.html", {
        "version": version,
        "accepted": accepted,
        "acceptance": acceptance,
        "consent": consent,
        "form": form,
        "media_fields": group(ParticipantConsent.MEDIA_FIELDS),
        "recruiter_fields": group(ParticipantConsent.RECRUITER_FIELDS),
    })


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
            # Captains plan around the timeline from this page, so it is
            # here rather than only on the dashboard.
            "key_dates": competition_service.key_dates(team.competition, team),
            "seats_free": matching_service.seats_free(team),
            # The application image, for the card the briefing points at.
            "app_submission": deployment_service.latest_for_team(team),
            "app_passing": deployment_service.last_passing(team),
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
    return stored_files.serve(
        request, profile_obj.identity_document, label="identity document"
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
    """Organiser team list, narrowed by side, track, status and state.

    Filtering lives in `services.roster_filters` so the CSV an organiser
    downloads is the same set they are looking at -- the export used to
    dump every team regardless of the filters on screen.
    """
    from services import roster_filters

    params = request.GET
    teams = roster_filters.teams(params)
    total = Team.objects.count()

    return render(
        request,
        "accounts/teams.html",
        {
            "teams": teams,
            "shown": teams.count(),
            "total": total,
            "filters": roster_filters.TEAM_FILTERS,
            "states": roster_filters.COMPLETENESS,
            "active": roster_filters.active(params),
            "params": params,
            "side": params.get("side", ""),
            "track": params.get("track", ""),
            "status": params.get("status", ""),
            "state": params.get("state", ""),
            "institution": params.get("institution", ""),
            "q": params.get("q", ""),
            "export_query": roster_filters.querystring(params),
            "counts": {
                s: Team.objects.filter(status=s).count() for s, _ in Team.Status.choices
            },
            "side_counts": {
                t: Team.objects.filter(team_type=t).count()
                for t, _ in Team.TeamType.choices
            },
        },
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
            # A brief no longer implies an enterprise, so only an explicit
            # choice sets one here; otherwise assign_cell balances the two.
            if d.get("enterprise"):
                team.enterprise = d["enterprise"]
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
    elif d["decision"] == Team.Status.REJECTED:
        team.status = Team.Status.REJECTED
        team.rejection_reason = d["rejection_reason"].strip()
        team.save()  # notifications/signals -> registration_rejected
        messages.success(request, f"{team.team_name} rejected; the captain has been told why.")
    elif d["decision"] == Team.Status.UNDER_REVIEW:
        # An internal marker, so two organisers do not read the same
        # registration. Deliberately silent: the team has done its part and
        # hearing "we have started looking" is noise.
        team.status = Team.Status.UNDER_REVIEW
        team.save()
        messages.success(
            request,
            f"{team.team_name} marked as under review. The team is not notified "
            f"and its roster stays locked.",
        )
    else:
        # Returned for edits. Not a rejection: the roster unlocks and the
        # team is expected to resubmit, so it says so in its own words.
        note = d["rejection_reason"].strip()
        team.status = Team.Status.REGISTERED
        team.rejection_reason = note
        team.save()
        cat.registration_returned(team, note=note)
        messages.success(
            request,
            f"{team.team_name} returned for edits; the captain has been told what to change.",
        )
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


@login_required
@staff_required
def storage_status(request):
    """Which file store the process serving uploads is actually using.

    Separate from `manage.py check_media` on purpose: that runs in a
    shell, which on Render is a fresh process with the current
    environment, while the workers handling uploads keep the environment
    they booted with. After adding the S3 variables the two disagree
    until the service restarts, and the symptom -- uploads that succeed
    and never appear in the bucket -- gives no hint which side is wrong.

    POST runs a live write/read/delete through this process.
    """
    return render(
        request,
        "accounts/storage_status.html",
        {"report": storage_diagnostics.report(probe=request.method == "POST")},
    )


# ------------------------------------------------------- team matching


@verified_required
def find_team(request):
    """For somebody with no team: declare a side and ask to be placed.

    The old route here told members to wait for their captain's
    invitation, which is a dead end for anybody no captain knows. This
    puts them where captains with a free seat can see them.
    """
    if _user_has_team(request.user):
        return redirect("accounts:team_requests")

    profile_obj = _profile(request.user)
    if request.method == "POST":
        if request.POST.get("action") == "leave":
            matching_service.leave_pool(request.user)
            messages.info(request, "You have been taken off the list.")
        else:
            try:
                matching_service.join_pool(request.user, request.POST.get("side", ""))
            except matching_service.MatchingError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "You are on the list. Captains with a free seat on that side can "
                    "now see you and send an invitation.",
                )
        return redirect("accounts:find_team")

    competition = Competition.objects.order_by("-start_date").first()
    return render(
        request,
        "accounts/find_team.html",
        {
            "profile": profile_obj,
            "invitations": team_service.open_invitations_for(request.user),
            "outstanding": outstanding_for_user(request.user),
            "key_dates": competition_service.key_dates(competition) if competition else [],
            "open_teams": (
                matching_service.teams_with_seats(
                    competition, side=matching_service.SIDE_FOR_ROLE.get(profile_obj.role)
                ).count()
                if competition and profile_obj.seeking_team else 0
            ),
        },
    )


@login_required
def recruit(request):
    """For a captain: people looking for a place on this team's side."""
    team = team_service.team_for(request.user)
    if not team:
        return redirect("accounts:choose_role")
    if not team_service.is_captain_of(request.user, team):
        messages.error(request, "Only the captain can invite members.")
        return redirect("accounts:team_requests")

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("open", "close"):
            try:
                matching_service.set_open_to_recruits(team, action == "open")
            except team_service.TeamRuleError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    "Your team is listed to people looking for a place."
                    if team.open_to_recruits
                    else "Your team is no longer shown to people looking for a place.",
                )
        elif action == "invite":
            try:
                invitation = team_service.invite(
                    team, request.POST.get("email", ""), request.user
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
                    invitee_user=User.objects.filter(
                        email__iexact=invitation.email
                    ).first(),
                )
                messages.success(request, f"Invitation sent to {invitation.email}.")
        return redirect("accounts:recruit")

    return render(
        request,
        "accounts/recruit.html",
        {
            "team": team,
            "pool": matching_service.pool_for(team) if team.open_to_recruits else [],
            "seats_free": matching_service.seats_free(team),
            "max_members": team_service.MAX_MEMBERS,
            "is_locked": team.is_locked,
        },
    )


@login_required
@staff_required
def consent_register(request):
    """Who has agreed to what, and when.

    Two consents, deliberately reported side by side but never conflated:
    the data-privacy notice ticked at registration, and the code of
    conduct accepted on its own page. One is permission to process
    somebody's data, the other is agreement to the rules, and an auditor
    asks about them separately.

    `?missing=1` narrows it to the people still owing one, which is the
    view that is actually actionable before a deadline.
    """
    current = PolicyVersion.current()
    terms = PolicyVersion.current(PolicyVersion.Kind.TERMS)
    accepted_ids = set()
    if current:
        accepted_ids = set(
            current.acceptances.values_list("user_id", flat=True)
        )
    terms_ids = set(
        terms.acceptances.values_list("user_id", flat=True)
    ) if terms else set()
    # One query for the lanyards rather than one per person: the check-in
    # desk opens this with the whole field on it.
    consents = {
        c.user_id: c for c in ParticipantConsent.objects.all()
    }

    rows = []
    for profile_obj in (
        account_service.competitor_accounts()
        .select_related("profile")
        .order_by("username")
    ):
        profile = getattr(profile_obj, "profile", None)
        consent = consents.get(profile_obj.pk)
        rows.append({
            "user": profile_obj,
            "privacy_at": getattr(profile, "privacy_accepted_at", None),
            "privacy_ip": getattr(profile, "privacy_accepted_ip", None),
            "conduct": profile_obj.pk in accepted_ids,
            "terms": profile_obj.pk in terms_ids,
            # What the desk issues. Shown for everybody, because "no row"
            # and "standard lanyard" must not look the same.
            "lanyard": consent.lanyard if consent else "Standard",
            "black_lanyard": bool(consent and consent.black_lanyard),
            "recruiters": bool(consent and consent.shares_with_recruiters),
            "team": team_service.team_for(profile_obj),
        })

    if request.GET.get("missing"):
        rows = [
            r for r in rows
            if not r["privacy_at"] or not r["conduct"] or not r["terms"]
        ]
    if request.GET.get("black"):
        rows = [r for r in rows if r["black_lanyard"]]

    return render(
        request,
        "accounts/consent_register.html",
        {
            "rows": rows,
            "policy": current,
            "terms": terms,
            "missing_only": bool(request.GET.get("missing")),
            "black_only": bool(request.GET.get("black")),
            "complete": sum(
                1 for r in rows if r["privacy_at"] and r["conduct"] and r["terms"]
            ),
            "black_lanyards": sum(1 for r in rows if r["black_lanyard"]),
        },
    )


@login_required
@staff_required
def consent_export(request):
    """The same register as CSV, for the record outside this platform."""
    import csv

    from django.http import HttpResponse

    current = PolicyVersion.current()
    accepted = {}
    if current:
        accepted = dict(
            current.acceptances.values_list("user_id", "accepted_at")
        )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="jengasec-consent.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "username", "full name", "email", "team",
        "privacy notice accepted", "privacy notice IP",
        f"code of conduct v{current.version if current else '-'} accepted",
    ])
    for user in (
        account_service.competitor_accounts()
        .select_related("profile")
        .order_by("username")
    ):
        profile = getattr(user, "profile", None)
        team = team_service.team_for(user)
        writer.writerow([
            user.username,
            user.get_full_name(),
            user.email,
            team.team_name if team else "",
            _stamp(getattr(profile, "privacy_accepted_at", None)),
            getattr(profile, "privacy_accepted_ip", "") or "",
            _stamp(accepted.get(user.pk)),
        ])
    return response


def _stamp(value):
    """A timestamp a spreadsheet will sort, or blank."""
    from django.utils import timezone

    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else ""


@login_required
def team_edit(request):
    """Let a captain correct their own team's details.

    There was no route for this: institution and track were set at
    creation and then unreachable, so a captain who picked the wrong one
    -- or left the institution blank -- had nowhere to go. Locked while
    organisers review, like every other roster change.
    """
    team = team_service.team_for(request.user)
    if not team:
        return redirect("accounts:choose_role")
    if not team_service.is_captain_of(request.user, team):
        messages.error(request, "Only the captain can change the team's details.")
        return redirect("accounts:team_requests")

    if team.is_locked:
        messages.error(
            request,
            f"{team.team_name} is {team.get_status_display().lower()} and its details "
            f"are frozen while organisers review it. Ask an organiser to return it "
            f"if something needs correcting.",
        )
        return redirect("accounts:team_requests")

    if request.method == "POST":
        form = TeamEditForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, f"{team.team_name} updated.")
            return redirect("accounts:team_requests")
    else:
        form = TeamEditForm(instance=team)

    return render(
        request,
        "accounts/team_edit.html",
        {"form": form, "team": team},
    )


@login_required
@staff_required
def member_list(request):
    """Every person on a team, filtered the same way as the team list.

    Distinct from `user_list`, which is accounts. This is rosters: one
    row per seat, so an organiser checking who is on which team sees the
    team on every line and can click straight through to the account.
    """
    from services import roster_filters

    params = request.GET
    members = roster_filters.members(params)
    return render(
        request,
        "accounts/members.html",
        {
            "members": members,
            "shown": members.count(),
            "total": TeamMember.objects.count(),
            "filters": roster_filters.TEAM_FILTERS,
            "states": roster_filters.MEMBER_STATES,
            "roles": TeamMember.MemberRole.choices,
            "active": roster_filters.active(params),
            "params": params,
            "side": params.get("side", ""),
            "track": params.get("track", ""),
            "status": params.get("status", ""),
            "state": params.get("state", ""),
            "role": params.get("role", ""),
            "institution": params.get("institution", ""),
            "q": params.get("q", ""),
            "export_query": roster_filters.querystring(params),
            "export_columns": [(k, v[0]) for k, v in EXPORT_COLUMNS.items()],
            "default_columns": DEFAULT_COLUMNS,
        },
    )


# Columns an organiser may pick for a roster export. Key -> (header, getter).
EXPORT_COLUMNS = {
    "team": ("Team", lambda m: m.team.team_name),
    "identifier": ("Team ID", lambda m: m.team.team_identifier or ""),
    "side": ("Side", lambda m: m.team.get_team_type_display()),
    "track": ("Track", lambda m: m.team.get_track_display() if m.team.track else ""),
    "team_status": ("Team status", lambda m: m.team.get_status_display()),
    "institution": ("Institution", lambda m: m.team.institution),
    "name": ("Name", lambda m: m.student_name),
    "email": ("Email", lambda m: m.email),
    "role": ("Role", lambda m: m.get_role_display()),
    "specialist": ("Security specialist", lambda m: "yes" if m.is_security_specialist else "no"),
    "username": ("Username", lambda m: m.user.username if m.user else ""),
    "phone": ("Phone", lambda m: getattr(getattr(m.user, "profile", None), "phone", "") or ""),
    "student_number": ("Student number", lambda m: getattr(getattr(m.user, "profile", None), "student_number", "") or ""),
    "programme": ("Programme", lambda m: getattr(getattr(m.user, "profile", None), "programme", "") or ""),
    "verified": ("Email verified", lambda m: _verified(m)),
    "has_id": ("ID uploaded", lambda m: _has_id(m)),
}

DEFAULT_COLUMNS = ["team", "side", "track", "name", "email", "role", "institution"]


def _verified(member):
    profile = getattr(member.user, "profile", None)
    if profile is None:
        return "no account"
    return "yes" if profile.is_email_verified else "no"


def _has_id(member):
    profile = getattr(member.user, "profile", None)
    if profile is None:
        return "no account"
    return "yes" if profile.identity_document else "no"


@login_required
@staff_required
def member_export(request):
    """The filtered roster, with the columns the organiser picked.

    Honours exactly the filters on screen -- same `roster_filters.members`
    call as the list -- so what downloads is what was being looked at.
    """
    import csv

    from django.http import HttpResponse

    from services import roster_filters

    chosen = [c for c in request.GET.getlist("columns") if c in EXPORT_COLUMNS]
    if not chosen:
        chosen = DEFAULT_COLUMNS

    members = roster_filters.members(request.GET)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="jengasec-roster.csv"'
    writer = csv.writer(response)
    writer.writerow([EXPORT_COLUMNS[c][0] for c in chosen])
    for member in members:
        writer.writerow([EXPORT_COLUMNS[c][1](member) for c in chosen])
    return response
