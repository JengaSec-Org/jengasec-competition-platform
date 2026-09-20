"""Template context shared by every page.

`user_roles` gates the sidebar sections by role; `competition_phase`
gates the links that only make sense once the event is running.
"""

TEAM_ROLES = {"blue_team", "red_team"}


def user_roles(request):
    if request.user.is_authenticated:
        return {"user_roles": set(request.user.groups.values_list("name", flat=True))}
    return {"user_roles": set()}


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
