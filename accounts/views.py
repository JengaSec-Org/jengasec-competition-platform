from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from notifications.models import Notification

from .forms import TeamCreateForm, TeamJoinForm, UserRegistrationForm
from .models import Team, TeamJoinRequest, TeamMember


def _user_has_team(user):
    return Team.objects.filter(captain=user).exists() or TeamMember.objects.filter(user=user).exists()


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard:main_dashboard")

    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("accounts:choose_role")
    else:
        form = UserRegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


@login_required
def choose_role(request):
    if _user_has_team(request.user):
        return redirect("dashboard:main_dashboard")
    return render(request, "accounts/choose_role.html")


@login_required
def team_form(request):
    if _user_has_team(request.user):
        return redirect("dashboard:main_dashboard")

    if request.method == "POST":
        form = TeamCreateForm(request.POST)
        if form.is_valid():
            team = form.save(commit=False)
            team.captain = request.user
            team.save()
            messages.success(request, f"Team '{team.team_name}' created.")
            return redirect("dashboard:main_dashboard")
    else:
        form = TeamCreateForm()

    return render(request, "accounts/team_form.html", {"form": form})


@login_required
def choose_team(request):
    if _user_has_team(request.user):
        return redirect("dashboard:main_dashboard")

    pending_request = TeamJoinRequest.objects.filter(
        user=request.user, status=TeamJoinRequest.PENDING
    ).select_related("team").first()
    if pending_request:
        return render(request, "accounts/choose_team.html", {"pending_request": pending_request})

    if request.method == "POST":
        form = TeamJoinForm(request.POST)
        if form.is_valid():
            team = form.cleaned_data["team"]
            if team:
                TeamJoinRequest.objects.create(user=request.user, team=team)
                if team.captain:
                    Notification.objects.create(
                        user=team.captain,
                        type=Notification.Type.GENERAL,
                        subject="New team join request",
                        body=(
                            f"{request.user.get_full_name() or request.user.username} "
                            f"requested to join {team.team_name}."
                        ),
                    )
                messages.success(request, f"Request to join '{team.team_name}' sent.")
            return redirect("dashboard:main_dashboard")
    else:
        form = TeamJoinForm()

    return render(request, "accounts/choose_team.html", {"form": form})


@login_required
def team_requests(request):
    team = Team.objects.filter(captain=request.user).first()
    if not team:
        return redirect("dashboard:main_dashboard")

    pending = team.join_requests.filter(status=TeamJoinRequest.PENDING).select_related("user")
    members = team.members.select_related("user")
    return render(
        request,
        "accounts/team_requests.html",
        {"team": team, "join_requests": pending, "members": members},
    )


@login_required
def team_request_decide(request, request_id, decision):
    team = Team.objects.filter(captain=request.user).first()
    join_request = get_object_or_404(
        TeamJoinRequest, id=request_id, team=team, status=TeamJoinRequest.PENDING
    )

    if request.method == "POST" and decision in ("approve", "reject"):
        join_request.decided_at = timezone.now()
        if decision == "approve":
            join_request.status = TeamJoinRequest.APPROVED
            TeamMember.objects.get_or_create(
                team=team,
                user=join_request.user,
                defaults={
                    "student_name": join_request.user.get_full_name() or join_request.user.username,
                    "email": join_request.user.email,
                    "role": TeamMember.MemberRole.MEMBER,
                },
            )
            TeamJoinRequest.objects.filter(
                user=join_request.user, status=TeamJoinRequest.PENDING
            ).exclude(id=join_request.id).update(
                status=TeamJoinRequest.REJECTED, decided_at=timezone.now()
            )
            Notification.objects.create(
                user=join_request.user,
                type=Notification.Type.GENERAL,
                subject="Join request approved",
                body=f"Your request to join {team.team_name} was approved.",
            )
        else:
            join_request.status = TeamJoinRequest.REJECTED
            Notification.objects.create(
                user=join_request.user,
                type=Notification.Type.GENERAL,
                subject="Join request declined",
                body=f"Your request to join {team.team_name} was declined.",
            )
        join_request.save()

    return redirect("accounts:team_requests")


@login_required
def team_member_remove(request, member_id):
    team = Team.objects.filter(captain=request.user).first()
    if not team:
        return redirect("dashboard:main_dashboard")

    member = get_object_or_404(TeamMember, id=member_id, team=team)

    if request.method == "POST":
        removed_user = member.user
        student_name = member.student_name
        member.delete()
        if removed_user:
            Notification.objects.create(
                user=removed_user,
                type=Notification.Type.GENERAL,
                subject="Removed from team",
                body=f"You were removed from {team.team_name}.",
            )
        messages.success(request, f"{student_name} was removed from the team.")

    return redirect("accounts:team_requests")