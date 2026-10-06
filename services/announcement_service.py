"""Sending an organiser's own message to the competition.

Two front ends need this: the `send_announcement` management command and
the staff console form. The logic lives here so neither reimplements it
-- an announcement cannot be recalled, and two code paths with slightly
different audience rules is exactly how the wrong people get emailed.

`announce()` is the whole operation: pick the audience, deliver it in-app
and by email, and optionally pin it to the matching dashboards.
"""
from django.db import transaction
from django.utils.text import slugify

from accounts.models import Team
from competitions.models import Competition
from notifications.models import Announcement
from services import notification_catalogue as cat

# The three audiences, mapped to the team side they select. `all` matches
# every side, which is why it maps to None rather than a team type.
SIDES = {
    "red": Team.TeamType.RED,
    "blue": Team.TeamType.BLUE,
    "all": None,
}


class AnnouncementError(Exception):
    """The announcement cannot be sent as asked."""


def audience(side, competition=None):
    """Teams that would receive an announcement to `side`.

    Withdrawn teams are excluded: they have left, and an event notice is
    not something they need.
    """
    if side not in SIDES:
        raise AnnouncementError(f"Unknown audience {side!r}.")
    competition = competition or current_competition()
    teams = Team.objects.filter(competition=competition).exclude(
        status=Team.Status.WITHDRAWN
    )
    if SIDES[side] is not None:
        teams = teams.filter(team_type=SIDES[side])
    return teams.select_related("competition")


def current_competition():
    competition = Competition.objects.order_by("-start_date").first()
    if competition is None:
        raise AnnouncementError("No competition exists to announce to.")
    return competition


def preview(side, subject, body, competition=None):
    """Who it would reach, without sending anything."""
    competition = competition or current_competition()
    teams = list(audience(side, competition))
    return {
        "competition": competition,
        "teams": teams,
        "team_count": len(teams),
        "recipient_count": sum(
            t.members.exclude(user__isnull=True).count() for t in teams
        ),
        "subject": subject.strip(),
        "body": body.strip(),
        "slug": default_slug(subject),
    }


def default_slug(subject):
    return slugify(subject)[:40]


@transaction.atomic
def announce(side, subject, body, *, slug="", pin=False, expires_at=None,
             competition=None):
    """Deliver the announcement. Returns a summary of what happened."""
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject:
        raise AnnouncementError("An announcement needs a subject.")
    if not body:
        raise AnnouncementError("An announcement needs a body.")

    competition = competition or current_competition()
    slug = (slug or "").strip() or default_slug(subject)
    teams = list(audience(side, competition))
    if not teams:
        raise AnnouncementError(f"No {side} teams to announce to.")

    reached = 0
    for team in teams:
        reached += cat.announcement(team, subject, body, slug) or 0

    pinned = None
    if pin:
        pinned, _ = Announcement.objects.update_or_create(
            competition=competition,
            audience=side,
            title=subject,
            defaults={"body": body, "is_active": True, "expires_at": expires_at},
        )

    return {
        "competition": competition,
        "team_count": len(teams),
        "reached": reached,
        "slug": slug,
        "pinned": pinned,
    }
