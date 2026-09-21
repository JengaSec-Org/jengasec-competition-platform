"""Team membership and allocation (Entry Guide sections 2, 4 and 6).

Keeps together the things that must always happen together: seating a
person on a team and giving them the platform role that team implies;
issuing a team identifier; assigning a cell within an enterprise.
"""
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from accounts.models import Team, TeamInvitation, TeamMember, UserProfile
from competitions.constants import EDITION, Enterprise, Track, cell_prefix

# Entry Guide section 2: teams of three to four, plus one optional reserve.
MIN_MEMBERS = 3
MAX_MEMBERS = 4
MAX_RESERVES = 1

# A team's side decides its people's platform role, and therefore which
# dashboard they see and which sidebar sections open for them.
TEAM_TYPE_ROLE = {
    Team.TeamType.BLUE: UserProfile.Role.BLUE_TEAM,
    Team.TeamType.RED: UserProfile.Role.RED_TEAM,
}


class TeamRuleError(ValueError):
    """A Guide rule was broken. The message is written for the person who
    tried, and is safe to show verbatim."""


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------

def assign_platform_role(user, team):
    """Give `user` the blue_team / red_team role implied by `team`."""
    role = TEAM_TYPE_ROLE.get(team.team_type)
    if role is None:
        return
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if profile.role in (UserProfile.Role.JUDGE, UserProfile.Role.PARTNER, UserProfile.Role.ADMIN):
        return
    if profile.role != role:
        profile.role = role
        profile.save()


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


def playing_count(team):
    """Members who count toward the three-to-four rule (reserve excluded)."""
    return team.members.exclude(role=TeamMember.MemberRole.RESERVE).count()


def check_unlocked(team, action="change the roster"):
    """Submitted and approved registrations are frozen (Guide section 6, step 9)."""
    if team.is_locked:
        raise TeamRuleError(
            f"{team.team_name} has been submitted for review; you cannot {action} "
            f"until the organisers decide. Contact them if something must change."
        )


def check_can_join(team, user, as_reserve=False):
    """Raise TeamRuleError if `user` may not be seated on `team`."""
    profile = getattr(user, "profile", None)
    if profile is not None and not profile.is_email_verified:
        raise TeamRuleError(
            "Verify your email address before joining a team -- the link is in your inbox."
        )
    existing = team_for(user)
    if existing is not None and existing.pk != team.pk:
        raise TeamRuleError(
            f"{user.get_full_name() or user.username} is already on "
            f"{existing.team_name}. One person, one team, one side."
        )
    if as_reserve:
        if team.members.filter(role=TeamMember.MemberRole.RESERVE).count() >= MAX_RESERVES:
            raise TeamRuleError("The team already has its one reserve.")
    elif playing_count(team) >= MAX_MEMBERS:
        raise TeamRuleError(
            f"{team.team_name} already has {MAX_MEMBERS} members. "
            f"A fifth person can only join as the reserve."
        )


@transaction.atomic
def create_team_for_captain(form, captain):
    """Registering as a captain creates the team *and* seats the captain on it."""
    if team_for(captain) is not None:
        raise TeamRuleError("You are already on a team.")
    profile = getattr(captain, "profile", None)
    if profile is not None and not profile.is_email_verified:
        raise TeamRuleError("Verify your email address before creating a team.")
    team = form.save(commit=False)
    team.captain = captain
    if team.application_brief_id and not team.application_choice:
        team.application_choice = team.application_brief.name
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
def add_member(team, user, as_reserve=False):
    """Seat a user on a team, with the matching platform role.

    Enforces the size rule and one-person-one-team. Returns (member, created).
    """
    check_can_join(team, user, as_reserve=as_reserve)
    member, created = TeamMember.objects.get_or_create(
        team=team,
        user=user,
        defaults={
            "student_name": user.get_full_name() or user.username,
            "email": user.email,
            "role": TeamMember.MemberRole.RESERVE if as_reserve else TeamMember.MemberRole.MEMBER,
        },
    )
    assign_platform_role(user, team)
    return member, created


# ---------------------------------------------------------------------------
# Invitations (Guide section 6, steps 6-7)
# ---------------------------------------------------------------------------

@transaction.atomic
def invite(team, email, invited_by, as_reserve=False):
    """Create a seven-day invitation. Returns it, or raises TeamRuleError."""
    check_unlocked(team, "invite anyone")
    email = email.strip().lower()
    if not email:
        raise TeamRuleError("Enter an email address.")
    if team.members.filter(email__iexact=email).exists():
        raise TeamRuleError(f"{email} is already on the roster.")
    if team.invitations.filter(email__iexact=email, status=TeamInvitation.Status.PENDING).exists():
        raise TeamRuleError(f"{email} already has a pending invitation.")
    if not as_reserve and playing_count(team) + pending_invitation_count(team) >= MAX_MEMBERS:
        raise TeamRuleError(
            f"With pending invitations counted, the team would exceed {MAX_MEMBERS} members."
        )
    return TeamInvitation.objects.create(
        team=team,
        email=email,
        token=secrets.token_urlsafe(32),
        invited_by=invited_by,
        expires_at=timezone.now() + timedelta(days=TeamInvitation.EXPIRY_DAYS),
    )


def pending_invitation_count(team):
    return team.invitations.filter(
        status=TeamInvitation.Status.PENDING, expires_at__gt=timezone.now()
    ).count()


def accept_invitation(invitation, user):
    """Seat `user` from an open invitation addressed to their email.

    The expiry stamp happens *before* the atomic block on purpose: a
    TeamRuleError raised inside it would roll the stamp back.
    """
    if not invitation.is_open:
        if invitation.status == TeamInvitation.Status.PENDING:
            invitation.status = TeamInvitation.Status.EXPIRED
            invitation.save(update_fields=["status"])
        raise TeamRuleError("This invitation has expired or was withdrawn. Ask your captain for a new one.")
    if (user.email or "").lower() != invitation.email.lower():
        raise TeamRuleError(
            f"This invitation was sent to {invitation.email}. Sign in with that address to accept it."
        )
    return _seat_from_invitation(invitation, user)


@transaction.atomic
def _seat_from_invitation(invitation, user):
    check_unlocked(invitation.team, "join it")
    member, _ = add_member(invitation.team, user)
    invitation.status = TeamInvitation.Status.ACCEPTED
    invitation.accepted_by = user
    invitation.decided_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_by", "decided_at"])
    return member


def decline_invitation(invitation, user):
    if invitation.status == TeamInvitation.Status.PENDING:
        invitation.status = TeamInvitation.Status.DECLINED
        invitation.decided_at = timezone.now()
        invitation.accepted_by = user
        invitation.save(update_fields=["status", "decided_at", "accepted_by"])


def open_invitations_for(user):
    """Invitations waiting for this user's email address."""
    if not user.email:
        return TeamInvitation.objects.none()
    return TeamInvitation.objects.filter(
        email__iexact=user.email,
        status=TeamInvitation.Status.PENDING,
        expires_at__gt=timezone.now(),
    ).select_related("team", "team__competition", "invited_by")


def remove_member(team, member):
    """Captain takes somebody off the roster. Refused once submitted."""
    check_unlocked(team, "remove members")
    if member.user_id and member.user_id == team.captain_id:
        raise TeamRuleError("A captain cannot remove themselves from the team.")
    member.delete()


def set_deputy(team, member):
    """Make `member` the deputy captain (at most one; never the captain or
    the reserve). Setting the current deputy again clears it."""
    check_unlocked(team, "change the deputy")
    if member.role in (TeamMember.MemberRole.CAPTAIN, TeamMember.MemberRole.RESERVE):
        raise TeamRuleError("The captain and the reserve cannot be the deputy.")
    if member.role == TeamMember.MemberRole.DEPUTY:
        member.role = TeamMember.MemberRole.MEMBER
        member.save(update_fields=["role"])
        return None
    team.members.filter(role=TeamMember.MemberRole.DEPUTY).update(
        role=TeamMember.MemberRole.MEMBER
    )
    member.role = TeamMember.MemberRole.DEPUTY
    member.save(update_fields=["role"])
    return member


# ---------------------------------------------------------------------------
# Registration submission (Guide section 6, steps 9-11)
# ---------------------------------------------------------------------------

def tracks_for_side(team_type):
    """Track choices open to a side: (value, Guide track name)."""
    from competitions.constants import track_name

    return [(t.value, track_name(team_type, t.value)) for t in Track]


def registration_blockers(team):
    """Everything that stops this registration being submitted right now:
    the team-level items, then each member's own outstanding items.

    Returns a list of (member_or_None, item) so the page can group them.
    """
    from notifications.management.commands.send_outstanding_digest import outstanding_for_member
    from notifications.management.commands.send_registration_reminders import outstanding_for

    blockers = [(None, item) for item in outstanding_for(team)]
    for member in team.members.select_related("user", "user__profile"):
        for item in outstanding_for_member(member):
            blockers.append((member, item))
    return blockers


@transaction.atomic
def submit_registration(team, *, track_preferences, enterprise_preference,
                        skills_declaration, mentor=None):
    """Lock the registration and send it for review.

    Refuses while any blocker remains: the point of the checklist is that
    organisers never see a registration with a known gap.
    """
    if not team.can_submit:
        raise TeamRuleError(f"This registration is {team.get_status_display().lower()} and cannot be submitted again.")
    blockers = registration_blockers(team)
    if blockers:
        raise TeamRuleError(
            "The registration is not complete. Outstanding: "
            + "; ".join(item for _, item in blockers[:3])
            + (" ..." if len(blockers) > 3 else "")
        )
    open_tracks = {t for t, _ in tracks_for_side(team.team_type)}
    prefs = [t for t in track_preferences if t in open_tracks]
    if not prefs or len(set(prefs)) != len(prefs):
        raise TeamRuleError("Rank the tracks open to your side, each at most once.")
    declaration = (skills_declaration or "").strip()
    if not Team.SKILLS_MIN <= len(declaration) <= Team.SKILLS_MAX:
        raise TeamRuleError(
            f"The skills declaration must be {Team.SKILLS_MIN} to {Team.SKILLS_MAX} characters."
        )

    team.track_preferences = prefs
    team.track = prefs[0]
    if team.track != Track.APPLICATION:
        team.application_brief = None
    team.enterprise_preference = enterprise_preference or ""
    team.skills_declaration = declaration
    for key, value in (mentor or {}).items():
        setattr(team, f"mentor_{key}", value or "")
    team.status = Team.Status.SUBMITTED
    team.submitted_at = timezone.now()
    team.rejection_reason = ""
    team.save()

    from services import notification_catalogue as cat

    transaction.on_commit(lambda: cat.registration_submitted(team))
    return team


# ---------------------------------------------------------------------------
# Identifiers and cells (Guide sections 4, 6 step 12, 10)
# ---------------------------------------------------------------------------

def assign_identifier(team):
    """JS26-B-014: edition, side letter, sequence within edition + side.

    Issued once. Idempotent: an already-identified team keeps its number.
    """
    if team.team_identifier:
        return team.team_identifier
    side = "B" if team.team_type == Team.TeamType.BLUE else "R"
    prefix = f"{EDITION}-{side}-"
    taken = Team.objects.filter(team_identifier__startswith=prefix).values_list(
        "team_identifier", flat=True
    )
    highest = max((int(t.rsplit("-", 1)[-1]) for t in taken if t[-3:].isdigit()), default=0)
    team.team_identifier = f"{prefix}{highest + 1:03d}"
    team.save(update_fields=["team_identifier"])
    return team.team_identifier


def cell_taken(competition, enterprise, cell_id, exclude_pk=None):
    qs = Team.objects.filter(competition=competition, enterprise=enterprise, cell_id=cell_id)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def choose_enterprise(team):
    """Which JengaBank instance a team goes into.

    Application teams follow their brief -- unless that brief's cell is
    already taken in that enterprise, in which case the same brief in the
    other enterprise (every brief is built twice). Everyone else is
    balanced: whichever enterprise has fewer teams of this family.
    """
    if team.track == Track.APPLICATION and team.application_brief_id:
        brief = team.application_brief
        preferred = brief.enterprise or Enterprise.A
        other = Enterprise.B if preferred == Enterprise.A else Enterprise.A
        for ent in (preferred, other):
            if not cell_taken(team.competition, ent, brief.code, exclude_pk=team.pk):
                return ent
        raise TeamRuleError(
            f"Brief {brief.code} already has a builder in both enterprises. "
            f"Selection has filled this brief."
        )
    if team.enterprise:
        return team.enterprise
    family = team.cell_family
    counts = {e: 0 for e, _ in Enterprise.choices}
    rows = (
        Team.objects.filter(competition=team.competition, cell_id__startswith=family)
        .exclude(pk=team.pk)
        .values("enterprise")
        .annotate(n=Count("id"))
    )
    for r in rows:
        if r["enterprise"] in counts:
            counts[r["enterprise"]] = r["n"]
    return min(counts, key=lambda e: (counts[e], e))


def next_cell_code(competition, enterprise, family):
    """APP03 -> the next free number in that family within the enterprise."""
    used = Team.objects.filter(
        competition=competition, enterprise=enterprise, cell_id__startswith=family
    ).values_list("cell_id", flat=True)
    numbers = []
    for code in used:
        tail = code[len(family):]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"{family}{(max(numbers, default=0) + 1):02d}"


@transaction.atomic
def assign_cell(team, cell_id="", enterprise=""):
    """Put the team in an enterprise and give it a cell code.

    Application teams take their brief's code (APP03 is APP03 in both
    enterprises -- that is the point), one builder per enterprise. Other
    families get the next free number. Explicit `cell_id` / `enterprise`
    override the automatic pick and are checked for collisions.
    """
    if not team.track:
        raise TeamRuleError("The team has no track; assign one before a cell.")
    family = team.cell_family
    if cell_id:
        cell_id = cell_id.strip().upper()
        if not cell_id.startswith(family):
            raise TeamRuleError(f"Cell {cell_id} is not a {family} cell.")
        ent = enterprise or team.enterprise or choose_enterprise(team)
        if cell_taken(team.competition, ent, cell_id, exclude_pk=team.pk):
            raise TeamRuleError(f"{cell_id} is already allocated in {ent.upper()}.")
        team.enterprise, team.cell_id = ent, cell_id
    elif team.track == Track.APPLICATION and team.application_brief_id:
        team.enterprise = enterprise or choose_enterprise(team)
        team.cell_id = team.application_brief.code
        if cell_taken(team.competition, team.enterprise, team.cell_id, exclude_pk=team.pk):
            raise TeamRuleError(f"{team.cell_id} is already allocated in {team.enterprise_code}.")
    else:
        team.enterprise = enterprise or choose_enterprise(team)
        team.cell_id = next_cell_code(team.competition, team.enterprise, family)
    team.save(update_fields=["enterprise", "cell_id", "updated_at"])
    return team.cell_id


def suggested_cell(team):
    """What assign_cell would give, without saving. For the decision form."""
    if not team.track:
        return "", ""
    try:
        enterprise = choose_enterprise(team)
    except TeamRuleError:
        return "", ""
    if team.track == Track.APPLICATION and team.application_brief_id:
        return enterprise, team.application_brief.code
    return enterprise, next_cell_code(team.competition, enterprise, team.cell_family)
