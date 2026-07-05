"""Canonical role names for the platform.

Roles are Django auth Groups (created by accounts/migrations/0001).
Admins are identified by is_staff / is_superuser, not a group.
Pair 1: build registration/user-management on top of these.
"""
BLUE_TEAM = "blue_team"
RED_TEAM = "red_team"
JUDGE = "judge"
PARTNER = "partner"

ALL_ROLES = [BLUE_TEAM, RED_TEAM, JUDGE, PARTNER]


def user_in_role(user, role: str) -> bool:
    return user.is_authenticated and user.groups.filter(name=role).exists()
