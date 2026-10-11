"""Filtering the organiser's team and people lists.

One place, because the list on screen and the CSV an organiser downloads
have to be the same set. The export used to dump every team regardless
of what was on screen, which is the kind of mismatch nobody notices
until the wrong people get emailed.

Each filter is skipped when its parameter is absent, so the same
function serves "everything" and any combination of narrowing.
"""
from django.db.models import Count, Q

from accounts.models import Team, TeamInvitation, TeamMember, UserProfile
from competitions.constants import Track
from services import team_service

# What the organiser can narrow a team list by. Order is the order the
# controls appear.
TEAM_FILTERS = (
    ("side", "Side", Team.TeamType.choices),
    ("track", "Track", Track.choices),
    ("status", "Status", Team.Status.choices),
)

COMPLETENESS = (
    ("short", "Roster under four"),
    ("full", "Roster complete"),
    ("unverified", "Has an unverified member"),
    ("no_institution", "No lead institution"),
    ("pending_invites", "Has pending invitations"),
)


def teams(params, competition=None):
    """The team queryset for a set of GET parameters.

    Annotates the counts the list and the export both show, so neither
    has to re-query per row.
    """
    qs = (
        Team.objects.select_related("competition", "captain", "application_brief")
        .annotate(
            member_count=Count("members", distinct=True),
            playing_count=Count(
                "members",
                filter=~Q(members__role=TeamMember.MemberRole.RESERVE),
                distinct=True,
            ),
            pending_count=Count(
                "invitations",
                filter=Q(invitations__status=TeamInvitation.Status.PENDING),
                distinct=True,
            ),
        )
        .order_by("competition__name", "team_name")
    )
    if competition is not None:
        qs = qs.filter(competition=competition)

    side = params.get("side", "")
    if side:
        qs = qs.filter(team_type=side)

    track = params.get("track", "")
    if track:
        qs = qs.filter(track=track)

    status = params.get("status", "")
    if status:
        qs = qs.filter(status=status)

    institution = params.get("institution", "").strip()
    if institution:
        qs = qs.filter(institution__icontains=institution)

    query = params.get("q", "").strip()
    if query:
        # One box that searches the things an organiser actually types:
        # a team name, an identifier, a captain, or a member's email.
        qs = qs.filter(
            Q(team_name__icontains=query)
            | Q(team_identifier__icontains=query)
            | Q(captain__username__icontains=query)
            | Q(captain__email__icontains=query)
            | Q(members__email__icontains=query)
            | Q(members__student_name__icontains=query)
        ).distinct()

    state = params.get("state", "")
    if state == "short":
        qs = qs.filter(playing_count__lt=team_service.MAX_MEMBERS)
    elif state == "full":
        qs = qs.filter(playing_count__gte=team_service.MAX_MEMBERS)
    elif state == "unverified":
        qs = qs.filter(
            members__user__profile__email_verified_at__isnull=True,
            members__user__isnull=False,
        ).distinct()
    elif state == "no_institution":
        qs = qs.filter(Q(institution="") | Q(institution__isnull=True))
    elif state == "pending_invites":
        qs = qs.filter(pending_count__gt=0)

    return qs


def members(params, competition=None):
    """People, narrowed by the same controls plus their own.

    Driven from TeamMember rather than User so a row always has a team,
    which is what an organiser checking a roster is looking at.
    """
    qs = (
        TeamMember.objects.select_related(
            "team", "team__competition", "user", "user__profile"
        )
        .order_by("team__team_name", "student_name")
    )
    if competition is not None:
        qs = qs.filter(team__competition=competition)

    side = params.get("side", "")
    if side:
        qs = qs.filter(team__team_type=side)

    track = params.get("track", "")
    if track:
        qs = qs.filter(team__track=track)

    status = params.get("status", "")
    if status:
        qs = qs.filter(team__status=status)

    institution = params.get("institution", "").strip()
    if institution:
        qs = qs.filter(team__institution__icontains=institution)

    role = params.get("role", "")
    if role:
        qs = qs.filter(role=role)

    query = params.get("q", "").strip()
    if query:
        qs = qs.filter(
            Q(student_name__icontains=query)
            | Q(email__icontains=query)
            | Q(user__username__icontains=query)
            | Q(team__team_name__icontains=query)
        )

    state = params.get("state", "")
    if state == "unverified":
        qs = qs.filter(user__profile__email_verified_at__isnull=True)
    elif state == "no_account":
        qs = qs.filter(user__isnull=True)
    elif state == "no_id":
        qs = qs.filter(user__profile__identity_document="")
    elif state == "manual_check":
        qs = qs.filter(
            user__profile__manual_verification_required=True,
            user__profile__manual_verified_at__isnull=True,
        )

    return qs


MEMBER_STATES = (
    ("unverified", "Email not verified"),
    ("no_account", "No platform account"),
    ("no_id", "No ID document"),
    ("manual_check", "Awaiting institution check"),
)


def active(params, keys=("side", "track", "status", "state", "institution", "q", "role")):
    """The filters actually in use, for "showing X of Y" and the reset link."""
    return {k: params.get(k, "").strip() for k in keys if params.get(k, "").strip()}


def querystring(params, **overrides):
    """Rebuild the query string, so a filter link keeps the others.

    Without this, clicking "Blue" from a filtered list silently drops the
    institution and search the organiser had already typed.
    """
    from urllib.parse import urlencode

    current = {k: v for k, v in params.items() if v and k != "page"}
    current.update({k: v for k, v in overrides.items()})
    return urlencode({k: v for k, v in current.items() if v})
