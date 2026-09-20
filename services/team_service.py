"""Team membership business logic (Module 1: captains and members).

Keeps the two things that must always happen together in one place:
putting somebody on a team, and giving them the platform role that team
implies. Views that do one without the other produce a captain who lands
on the "role not assigned" page -- which is exactly what happened before
this module existed.
"""
from django.db import transaction

from accounts.models import Team, TeamMember, UserProfile

# A team's side decides its people's platform role, and therefore which
# dashboard they see and which sidebar sections open for them.
TEAM_TYPE_ROLE = {
    Team.TeamType.BLUE: UserProfile.Role.BLUE_TEAM,
    Team.TeamType.RED: UserProfile.Role.RED_TEAM,
}


def assign_platform_role(user, team):
    """Give `user` the blue_team / red_team role implied by `team`.

    Saving the profile syncs the auth Group, which is what the dashboard
    router and the sidebar actually check. Judges, partners and staff are
    never touched -- their role is not something a team can change.
    """
    role = TEAM_TYPE_ROLE.get(team.team_type)
    if role is None:
        return
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.role in (UserProfile.Role.JUDGE, UserProfile.Role.PARTNER, UserProfile.Role.ADMIN):
        return
    if profile.role != role:
        profile.role = role
        profile.save()


@transaction.atomic
def create_team_for_captain(form, captain):
    """Registering as a captain creates the team *and* seats the captain on it.

    The captain gets a TeamMember row (so the roster and every "all
    members" notification include them) and the platform role for the
    team's side. `accounts/signals.py` then adds the captain / team_member
    groups from those rows.
    """
    team = form.save(commit=False)
    team.captain = captain
    team.save()
    TeamMember.objects.get_or_create(
        team=team,
        user=captain,
        defaults={
            "student_name": captain.get_full_name() or captain.username,
            "email": captain.email,
            "role": TeamMember.MemberRole.CAPTAIN,
        },
    )
    assign_platform_role(captain, team)
    return team


@transaction.atomic
def add_member(team, user):
    """Seat an approved user on a team, with the matching platform role."""
    member, created = TeamMember.objects.get_or_create(
        team=team,
        user=user,
        defaults={
            "student_name": user.get_full_name() or user.username,
            "email": user.email,
            "role": TeamMember.MemberRole.MEMBER,
        },
    )
    assign_platform_role(user, team)
    return member, created


def team_for(user):
    """The team a user belongs to, as captain or member, or None."""
    team = Team.objects.filter(captain=user).select_related("competition").first()
    if team:
        return team
    membership = (
        TeamMember.objects.filter(user=user)
        .select_related("team", "team__competition")
        .first()
    )
    return membership.team if membership else None


def is_captain_of(user, team):
    return team is not None and team.captain_id == user.pk
