"""Putting unassigned competitors on teams that have a seat free.

Registration assumed every competitor arrives with a team. In practice a
lot of people sign up alone, and until now the platform had nothing for
them: `UserProfile.role` is set when somebody *joins* a team, so an
unassigned person could not even say which side they wanted, and
`choose_team` told them to wait for a captain's invitation -- which never
comes if no captain has their email.

Two opt-ins meet here. A person declares a side and asks to be placed
(`UserProfile.seeking_team`); a captain says their team will consider
strangers (`Team.open_to_recruits`). Neither is automatic: a team that
has its fourth lined up offline should not be handed somebody, and
nobody should be dropped onto a team they did not ask for.

The invitation itself is the existing one. Matching only decides who sees
whom; `team_service.invite` still does the seating, so every rule about
size, verification and locked registrations applies unchanged.
"""
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import Team, TeamMember, UserProfile
from services import team_service

# A person's declared side maps to the team type they can fill a seat on.
SIDE_FOR_ROLE = {
    UserProfile.Role.BLUE_TEAM: Team.TeamType.BLUE,
    UserProfile.Role.RED_TEAM: Team.TeamType.RED,
}


class MatchingError(Exception):
    """The person or the team cannot take part in matching."""


def join_pool(user, side):
    """Declare a side and ask to be placed on a team.

    Refused once the person is on a team: their side is then decided by
    the team, and two sources of truth for it is how someone ends up
    listed as looking for a place they already have.
    """
    if side not in SIDE_FOR_ROLE:
        raise MatchingError("Choose the blue side or the red side.")
    if team_service.team_for(user) is not None:
        raise MatchingError("You are already on a team.")
    profile = getattr(user, "profile", None)
    if profile is None:
        raise MatchingError("This account has no profile.")
    if not profile.is_email_verified:
        raise MatchingError(
            "Verify your email address first -- captains only see verified people."
        )

    profile.role = side
    profile.seeking_team = True
    profile.seeking_since = profile.seeking_since or timezone.now()
    profile.save(update_fields=["role", "seeking_team", "seeking_since"])
    profile.sync_role_group()
    return profile


def leave_pool(user):
    """Stop being listed. Keeps the declared side."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return None
    profile.seeking_team = False
    profile.seeking_since = None
    profile.save(update_fields=["seeking_team", "seeking_since"])
    return profile


def pool_for(team):
    """People a captain of `team` may invite, longest wait first.

    Scoped to the team's own side, because a blue team cannot seat
    someone who asked to play red. Anyone already on a team is excluded
    even if their flag was left set, so a stale flag cannot produce a
    listing that leads to a refused invitation.
    """
    if team is None:
        return UserProfile.objects.none()
    seated = TeamMember.objects.exclude(user__isnull=True).values("user_id")
    return (
        UserProfile.objects.filter(
            seeking_team=True,
            role=_role_for_side(team.team_type),
            user__is_active=True,
            email_verified_at__isnull=False,
        )
        .exclude(user_id__in=seated)
        .exclude(user__isnull=True)
        .select_related("user")
        .order_by("seeking_since", "user__username")
    )


def teams_with_seats(competition, side=None, open_only=True):
    """Teams short of a full roster, as a queryset with the count attached.

    `open_only` keeps it to captains who have opted in; organisers pass
    False to see every short team when the deadline is close.
    """
    teams = (
        Team.objects.filter(competition=competition)
        .exclude(status=Team.Status.WITHDRAWN)
        .annotate(
            playing=Count(
                "members",
                filter=~Q(members__role=TeamMember.MemberRole.RESERVE),
                distinct=True,
            )
        )
        .filter(playing__lt=team_service.MAX_MEMBERS)
    )
    if open_only:
        teams = teams.filter(open_to_recruits=True)
    if side is not None:
        teams = teams.filter(team_type=side)
    return teams.select_related("competition").order_by("team_name")


def seats_free(team):
    return max(team_service.MAX_MEMBERS - team_service.playing_count(team), 0)


def set_open_to_recruits(team, wanted):
    """The captain's toggle. Refused once the registration is locked."""
    team_service.check_unlocked(team, "change whether the team is open to recruits")
    team.open_to_recruits = bool(wanted)
    team.save(update_fields=["open_to_recruits"])
    return team


def on_joined_team(user):
    """Clear the flag once somebody is seated, wherever that happened.

    Called from the invitation-accepted path so the pool never lists a
    person a captain cannot actually invite.
    """
    UserProfile.objects.filter(user=user, seeking_team=True).update(
        seeking_team=False, seeking_since=None
    )


def _role_for_side(team_type):
    for role, side in SIDE_FOR_ROLE.items():
        if side == team_type:
            return role
    return ""


def summary(competition):
    """Counts for the organiser view and the reminder commands."""
    waiting = UserProfile.objects.filter(
        seeking_team=True, user__is_active=True
    ).exclude(user_id__in=TeamMember.objects.exclude(user__isnull=True).values("user_id"))
    short = teams_with_seats(competition, open_only=False)
    return {
        "waiting": waiting.count(),
        "waiting_blue": waiting.filter(role=UserProfile.Role.BLUE_TEAM).count(),
        "waiting_red": waiting.filter(role=UserProfile.Role.RED_TEAM).count(),
        "short_teams": short.count(),
        "open_teams": short.filter(open_to_recruits=True).count(),
        "seats_free": sum(seats_free(t) for t in short),
    }
