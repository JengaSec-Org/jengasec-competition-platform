"""Template context shared by every page.

`user_roles` gates the sidebar sections by role; `competition_phase`
gates the links that only make sense once the event is running.
"""

TEAM_ROLES = {"blue_team", "red_team"}


def user_roles(request):
    if request.user.is_authenticated:
        return {"user_roles": set(request.user.groups.values_list("name", flat=True))}
    return {"user_roles": set()}


def team_standing(request):
    """What the sidebar needs to know about this person's team.

    Three links depend on it -- "Find Me a Team" for somebody with none,
    and "Fill Your Seats" for a captain who is short -- and a link that
    only appears when it applies is worth one query. Costs nothing for
    anonymous visitors and staff without a team.
    """
    blank = {
        "has_no_team": False, "is_captain": False, "seats_free": 0,
        "team_locked": False, "submits_application": False,
    }
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return blank

    from services import deployment_service, matching_service, team_service

    team = team_service.team_for(user)
    if team is None:
        # Judges, partners and organisers are not short of a team; they
        # are not meant to have one.
        roles = set(user.groups.values_list("name", flat=True))
        competing = not (roles & {"judge", "partner"}) and not user.is_staff
        return {**blank, "has_no_team": competing}

    is_captain = team.captain_id == user.pk
    return {
        "has_no_team": False,
        "is_captain": is_captain,
        "seats_free": matching_service.seats_free(team) if is_captain else 0,
        # Links that bounce are worse than absent ones: the edit page
        # refuses a locked registration, so the link goes with it.
        "team_locked": team.is_locked,
        # Only Application Blue hands an image to the range, so only they
        # are offered the submission form.
        "submits_application": deployment_service.submits_application(team),
    }


def competition_phase(request):
    """The phase of the signed-in team's competition, or None.

    Costs one query, and only for users who are actually on a team —
    staff, judges and anonymous visitors short-circuit to None.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"competition_phase": None}

    roles = set(user.groups.values_list("name", flat=True))
    if not roles & TEAM_ROLES:
        return {"competition_phase": None}

    # Imported lazily: context processors load before the app registry
    # is ready during some management commands.
    from services.submission_service import teams_for

    team = teams_for(user).select_related("competition").first()
    return {"competition_phase": team.competition.phase if team else None}
