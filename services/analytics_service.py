"""Registration-phase analytics (Module 11).

Everything an organiser needs to answer "how is the turnout going, and
what is blocking it" from one page, computed on demand: the cohort is a
few hundred people, so there is nothing here worth caching or
denormalising, and a stale number during registration is worse than a
slow one.

The queries deliberately lean on the same helpers the rest of the
platform uses (`outstanding_for`, `outstanding_for_member`), so the
numbers on this page can never disagree with the checklist a captain is
looking at.
"""
from collections import Counter
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import PolicyAcceptance, PolicyVersion, Team, TeamInvitation, TeamMember, UserProfile
from competitions.constants import track_name
from competitions.models import ApplicationBrief
from submissions.models import Submission


def _members(competition):
    return TeamMember.objects.filter(team__competition=competition).select_related(
        "user", "user__profile", "team"
    )


def funnel(competition):
    """Where people are in the registration journey, in order.

    Each stage counts *people*, not teams, because the drop-off that
    matters is individual: accounts that never verify, members who never
    finish a profile.
    """
    members = list(_members(competition))
    linked = [m for m in members if m.user_id]
    verified = [m for m in linked if m.user.profile.is_email_verified]

    current_policy = PolicyVersion.current()
    accepted_ids = set()
    if current_policy:
        accepted_ids = set(
            PolicyAcceptance.objects.filter(version=current_policy).values_list("user_id", flat=True)
        )

    complete = [
        m for m in verified
        if m.user.profile.identity_document and m.user.profile.date_of_birth
        and m.user.profile.student_number and m.user.profile.repo_handle
    ]
    accepted = [m for m in verified if m.user_id in accepted_ids]
    submitted = [m for m in members if m.team.status in ("submitted", "approved")]

    stages = [
        ("On a roster", len(members)),
        ("Has an account", len(linked)),
        ("Email verified", len(verified)),
        ("Profile complete", len(complete)),
        ("Conduct accepted", len(accepted)),
        ("Registration submitted", len(submitted)),
    ]
    top = stages[0][1] or 1
    return [
        {"label": label, "count": count, "percent": round(count * 100 / top)}
        for label, count in stages
    ]


def team_status_counts(competition):
    rows = dict(
        Team.objects.filter(competition=competition)
        .values_list("status")
        .annotate(n=Count("id"))
    )
    return [
        {"status": value, "label": label, "count": rows.get(value, 0)}
        for value, label in Team.Status.choices
    ]


def track_breakdown(competition):
    """Teams per Guide track (side x specialisation)."""
    rows = (
        Team.objects.filter(competition=competition)
        .exclude(status=Team.Status.WITHDRAWN)
        .values("team_type", "track")
        .annotate(n=Count("id"))
    )
    out = []
    for row in rows:
        label = track_name(row["team_type"], row["track"]) or "Track not chosen"
        out.append({"label": label, "count": row["n"]})
    return sorted(out, key=lambda r: (-r["count"], r["label"]))


def institutions(competition, limit=12):
    """Which institutions the field is coming from, widest first."""
    counter = Counter()
    for team in Team.objects.filter(competition=competition).exclude(status=Team.Status.WITHDRAWN):
        counter[team.institution.strip() or "Not stated"] += 1
    return [{"label": name, "count": n} for name, n in counter.most_common(limit)]


def brief_demand(competition):
    """Proposals per application, so organisers can see the field cluster.

    A brief everyone wants and a brief nobody wants are both worth knowing
    before selection: places are per brief, so demand is what decides how
    competitive each one is.
    """
    briefs = (
        ApplicationBrief.objects.filter(competition=competition, is_open=True)
        # `proposals` and `teams` are reverse accessors on the model, so the
        # annotations need their own names.
        .annotate(
            proposal_count_=Count("proposals", distinct=True),
            team_count_=Count("teams", distinct=True),
        )
        .order_by("code")
    )
    return [
        {
            "code": b.code, "name": b.name, "cap": b.proposal_cap,
            "proposals": b.proposal_count_, "teams": b.team_count_,
            "percent": min(round((b.team_count_ or 0) * 100 / (b.proposal_cap or 1)), 100),
            "is_optional": b.is_optional,
        }
        for b in briefs
    ]


def blockers(competition, limit=10):
    """The outstanding items holding the most teams up, commonest first.

    Same source as the captain's own checklist, so the two never disagree.
    """
    from notifications.management.commands.send_outstanding_digest import outstanding_for_member
    from notifications.management.commands.send_registration_reminders import outstanding_for

    counter = Counter()
    teams = Team.objects.filter(
        competition=competition, status__in=(Team.Status.REGISTERED, Team.Status.REJECTED)
    ).prefetch_related("members__user__profile")
    for team in teams:
        for item in outstanding_for(team):
            counter[_generalise(item)] += 1
        for member in team.members.all():
            for item in outstanding_for_member(member):
                counter[_generalise(item)] += 1
    return [{"label": label, "count": n} for label, n in counter.most_common(limit)]


def _generalise(item):
    """Collapse the per-person wording into one countable phrase."""
    item = item.strip().rstrip(".")
    for needle, label in (
        ("verify your email", "Email not verified"),
        ("code of conduct", "Code of conduct not accepted"),
        ("student or national id", "Identity document not uploaded"),
        ("date of birth", "Date of birth missing"),
        ("student number", "Student number missing"),
        ("programme", "Programme missing"),
        ("repository handle", "Repository handle missing"),
        ("institution", "Institution missing"),
        ("phone number", "Captain/deputy phone missing"),
        ("security specialist", "No security specialist named"),
        ("members on the roster", "Roster below three members"),
        ("invitation", "Invitations still outstanding"),
        ("brief chosen", "No JengaBank brief chosen"),
        ("track chosen", "No track chosen"),
        ("create a platform account", "Invited member has no account"),
    ):
        if needle in item.lower():
            return label
    return item[:60]


def signups_by_day(competition, days=14):
    """Accounts created per day over the run-up, for the trend line."""
    since = timezone.now() - timedelta(days=days - 1)
    counter = Counter()
    for member in _members(competition):
        if member.user_id and member.user.date_joined >= since:
            counter[timezone.localtime(member.user.date_joined).date()] += 1
    today = timezone.localdate()
    series = [
        {"date": today - timedelta(days=offset), "count": counter.get(today - timedelta(days=offset), 0)}
        for offset in range(days - 1, -1, -1)
    ]
    peak = max((row["count"] for row in series), default=0) or 1
    for row in series:
        row["percent"] = round(row["count"] * 100 / peak)
    return series


def headline(competition):
    """The numbers that belong at the top of the page."""
    teams = Team.objects.filter(competition=competition).exclude(status=Team.Status.WITHDRAWN)
    members = _members(competition)
    verified = sum(1 for m in members if m.user_id and m.user.profile.is_email_verified)
    submitted = teams.filter(status__in=("submitted", "approved")).count()
    settings_row = competition.settings
    deadline = settings_row.registration_deadline
    hours_left = None
    if deadline:
        hours_left = max(int((deadline - timezone.now()).total_seconds() // 3600), 0)

    return {
        "teams": teams.count(),
        "people": members.count(),
        "verified": verified,
        "submitted": submitted,
        # Both states are waiting on an organiser's decision; a review that
        # has been picked up is still outstanding work.
        "awaiting_review": teams.filter(
            status__in=(Team.Status.SUBMITTED, Team.Status.UNDER_REVIEW)
        ).count(),
        "under_review": teams.filter(status=Team.Status.UNDER_REVIEW).count(),
        "approved": teams.filter(status=Team.Status.APPROVED).count(),
        "proposals": Submission.objects.filter(competition=competition).count(),
        "open_invitations": TeamInvitation.objects.filter(
            team__competition=competition, status=TeamInvitation.Status.PENDING,
            expires_at__gt=timezone.now(),
        ).count(),
        "unverified": members.filter(user__isnull=False).count() - verified,
        "manual_checks": UserProfile.objects.filter(
            manual_verification_required=True, manual_verified_at__isnull=True,
            user__team_memberships__team__competition=competition,
        ).distinct().count(),
        "registration_open": competition.registration_open,
        "deadline": deadline,
        "hours_left": hours_left,
    }


def overview(competition):
    """Everything the dashboard renders, in one call."""
    return {
        "competition": competition,
        "headline": headline(competition),
        "funnel": funnel(competition),
        "statuses": team_status_counts(competition),
        "tracks": track_breakdown(competition),
        "institutions": institutions(competition),
        "briefs": brief_demand(competition),
        "blockers": blockers(competition),
        "signups": signups_by_day(competition),
    }
