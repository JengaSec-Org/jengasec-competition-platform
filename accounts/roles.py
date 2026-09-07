"""Canonical role names for the platform.

Roles are Django auth Groups (created by accounts/migrations/0001 and 0004).
Admins are identified by is_staff / is_superuser, not a group.
Pair 1: build registration/user-management on top of these.

Two families, and they are not the same kind of thing:

* **Function roles** (blue_team, red_team, judge, partner) say what a person
  is here to do. Exactly one applies, and `UserProfile.role` is the source
  of truth -- saving a profile syncs the matching group.
* **Team roles** (captain, team_member) say where a person stands inside a
  team. They are derived from `Team.captain` and `TeamMember`, kept in step
  by signals in `accounts/signals.py`, and are *orthogonal* to the function
  roles: a blue-team captain holds `blue_team` and `captain` and
  `team_member` at once.

Never set a team role by hand. Change the team, and the group follows.
"""
BLUE_TEAM = "blue_team"
RED_TEAM = "red_team"
JUDGE = "judge"
PARTNER = "partner"

# What a person is here to do. Mutually exclusive.
ALL_ROLES = [BLUE_TEAM, RED_TEAM, JUDGE, PARTNER]

CAPTAIN = "captain"
TEAM_MEMBER = "team_member"

# Where a person stands in a team. Derived, additive, never mutually
# exclusive with the above -- kept separate so UserProfile.sync_role_group,
# which clears any managed group it is not targeting, never touches them.
TEAM_ROLES = [CAPTAIN, TEAM_MEMBER]


def user_in_role(user, role: str) -> bool:
    return user.is_authenticated and user.groups.filter(name=role).exists()


def is_captain(user) -> bool:
    """Captain of at least one team."""
    return user_in_role(user, CAPTAIN)


def is_team_member(user) -> bool:
    """On at least one team, as captain or member."""
    return user_in_role(user, TEAM_MEMBER)
