"""Profile creation, and keeping the team-role groups honest.

The `captain` and `team_member` groups are *derived* state: the truth is
`Team.captain` and the `TeamMember` rows, and these receivers make the
groups agree with it. Nothing should ever add or remove those groups by
hand -- change the team and the membership follows.

Every path recomputes from the database rather than applying a delta.
Deltas go wrong the moment two things change at once (a captain handing
over while also leaving the team); recomputing cannot drift.
"""
from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Team, TeamMember, UserProfile
from .roles import CAPTAIN, TEAM_MEMBER


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


# ---------------------------------------------------------------------------
# Team roles
# ---------------------------------------------------------------------------

def sync_team_roles(user):
    """Recompute one user's `captain` / `team_member` groups from the data.

    Captaincy implies membership: a captain is on the team whether or not
    anyone remembered to add a TeamMember row for them.
    """
    if user is None or not user.pk:
        return

    captains = Team.objects.filter(captain=user).exists()
    member = captains or TeamMember.objects.filter(user=user).exists()

    _set_group(user, CAPTAIN, captains)
    _set_group(user, TEAM_MEMBER, member)


def sync_team_roles_by_id(user_id):
    if not user_id:
        return
    User = Team._meta.get_field("captain").remote_field.model
    user = User.objects.filter(pk=user_id).first()
    sync_team_roles(user)


def _set_group(user, name, should_belong):
    belongs = user.groups.filter(name=name).exists()
    if should_belong and not belongs:
        group, _ = Group.objects.get_or_create(name=name)
        user.groups.add(group)
    elif belongs and not should_belong:
        user.groups.remove(Group.objects.get(name=name))


@receiver(pre_save, sender=Team)
def remember_previous_captain(sender, instance, **kwargs):
    """Stash the captain about to be replaced, so they can be demoted."""
    instance._previous_captain_id = (
        Team.objects.filter(pk=instance.pk).values_list("captain_id", flat=True).first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=Team)
def sync_captain_on_team_save(sender, instance, **kwargs):
    for user_id in {instance.captain_id, getattr(instance, "_previous_captain_id", None)}:
        sync_team_roles_by_id(user_id)


@receiver(post_delete, sender=Team)
def sync_captain_on_team_delete(sender, instance, **kwargs):
    sync_team_roles_by_id(instance.captain_id)


@receiver(pre_save, sender=TeamMember)
def remember_previous_member_user(sender, instance, **kwargs):
    instance._previous_user_id = (
        TeamMember.objects.filter(pk=instance.pk).values_list("user_id", flat=True).first()
        if instance.pk
        else None
    )


@receiver(post_save, sender=TeamMember)
def sync_member_on_save(sender, instance, **kwargs):
    for user_id in {instance.user_id, getattr(instance, "_previous_user_id", None)}:
        sync_team_roles_by_id(user_id)


@receiver(post_delete, sender=TeamMember)
def sync_member_on_delete(sender, instance, **kwargs):
    """Fires for cascade deletes too, so removing a team demotes its members."""
    sync_team_roles_by_id(instance.user_id)
