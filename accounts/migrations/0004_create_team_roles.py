"""Create the team-role groups: captain, team_member.

Separate from the four function roles in migration 0001 because these are
derived rather than chosen: `accounts/signals.py` keeps them in step with
`Team.captain` and `TeamMember`. The backfill below brings existing teams
into line, since those rows predate the signals.
"""
from django.db import migrations

TEAM_ROLES = ["captain", "team_member"]


def create_team_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Team = apps.get_model("accounts", "Team")
    TeamMember = apps.get_model("accounts", "TeamMember")

    captain_group, _ = Group.objects.get_or_create(name="captain")
    member_group, _ = Group.objects.get_or_create(name="team_member")

    # Backfill. Captaincy implies membership, so captains land in both.
    captain_ids = set(
        Team.objects.exclude(captain__isnull=True).values_list("captain_id", flat=True)
    )
    member_ids = set(
        TeamMember.objects.exclude(user__isnull=True).values_list("user_id", flat=True)
    ) | captain_ids

    if captain_ids:
        captain_group.user_set.add(*captain_ids)
    if member_ids:
        member_group.user_set.add(*member_ids)


def remove_team_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=TEAM_ROLES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_v1_schema_alignment"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_team_roles, remove_team_roles),
    ]
