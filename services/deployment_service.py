"""The rules for handing an application to the range.

Teams submit an image reference, not code and not a file (architecture
section 7.1). Everything that decides whether a submission is allowed,
what counts as passing, and what gets pinned at the freeze lives here;
the views stay thin, as elsewhere on the site.

Who may submit. Only **Application Blue** teams. Architecture section 7
is explicit -- "Only App Blue teams submit anything: their application"
-- because AI teams run their agents on their own machines and Red teams
attack from their own laptops. The Guide mentions an optional Red
toolbox, but k8s-red is held in reserve and deliberately not promised
before the event, so offering a toolbox form now would promise a thing
that may not exist.

The freeze. `CompetitionSettings.build_deadline` is the freeze: the deck
calls it "Build ends" and the Guide says the last passing image is locked
then. Until now that field enforced nothing anywhere on the site.
"""
import logging
import re

from django.db import transaction
from django.utils import timezone

from accounts.models import Team
from competitions.constants import Track
from services.range_service import namespace_for as range_namespace_for

logger = logging.getLogger(__name__)

# Up to two extra images, "for example a worker" (Image Submission Guide).
MAX_EXTRA_IMAGES = 2
NAMESPACE_RE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")


def namespace_for(team):
    """Return the range-exported cell namespace after validating it for Kubernetes."""
    namespace = range_namespace_for(team)
    if not NAMESPACE_RE.fullmatch(namespace):
        raise ValueError(
            f"Team {team.pk} needs a valid Kubernetes namespace or an enterprise and cell assignment."
        )
    return namespace


def service_ip_for(team):
    """Return an assigned unique IPv4 MetalLB service address for an app team."""
    from accounts.models import Team as TeamModel

    if team.team_type != TeamModel.TeamType.BLUE or team.track != Track.APPLICATION:
        return ""
    if not team.service_ip:
        raise ValueError(
            f"Application cell {team.cell_label or team.team_name} needs a reserved service IP."
        )
    return team.service_ip


class FreezePassed(ValueError):
    """Submission refused because the build deadline has passed."""


class NotEligible(ValueError):
    """This team does not submit an application image."""


def submits_application(team):
    """Whether this team hands an image to the range at all."""
    return bool(
        team
        and team.team_type == Team.TeamType.BLUE
        and team.track == Track.APPLICATION
    )


def freeze_deadline(competition):
    """When the last passing image is locked, or None if unset."""
    return competition.settings.build_deadline


def freeze_passed(competition, now=None):
    deadline = freeze_deadline(competition)
    return bool(deadline and (now or timezone.now()) > deadline)


def for_team(team):
    """Every attempt this team has made, newest first."""
    from deployments.models import AppSubmission

    return AppSubmission.objects.filter(team=team).select_related("competition")


def latest_for_team(team):
    return for_team(team).first()


def last_passing(team):
    """The attempt that would be pinned at the freeze, or None."""
    from deployments.models import AppSubmission

    return for_team(team).filter(status=AppSubmission.Status.HEALTHY).first()


def can_submit(team, now=None):
    """(allowed, reason) for whether this team may submit right now."""
    if not submits_application(team):
        return False, "Only Application Blue teams submit an application image."
    if team.status == Team.Status.WITHDRAWN:
        return False, "This team has withdrawn."
    competition = team.competition
    if competition.status not in (
        competition.Status.OPEN,
        competition.Status.IN_PROGRESS,
    ):
        return False, "This competition is not open."
    if freeze_passed(competition, now):
        when = timezone.localtime(freeze_deadline(competition))
        return False, f"The build deadline passed at {when:%d %b %Y %H:%M} EAT."
    return True, ""


@transaction.atomic
def submit(team, user, **fields):
    """Record one attempt. Raises `NotEligible` or `FreezePassed`.

    `pull_token` is encrypted on the way in and never stored in clear
    text. Earlier attempts are kept: the freeze has to be able to say
    which image was pinned, and a team that breaks a working submission
    must not lose the working one.
    """
    from deployments.models import AppSubmission

    allowed, reason = can_submit(team)
    if not allowed:
        raise (FreezePassed if freeze_passed(team.competition) else NotEligible)(reason)

    token = fields.pop("pull_token", "") or ""
    # select_for_update so two captains submitting at once cannot land on
    # the same attempt number and lose one to the unique constraint.
    taken = (
        AppSubmission.objects.select_for_update()
        .filter(team=team, competition=team.competition)
        .order_by("-attempt")
        .values_list("attempt", flat=True)
        .first()
    )
    submission = AppSubmission(
        team=team,
        competition=team.competition,
        attempt=(taken or 0) + 1,
        created_by=user,
        **fields,
    )
    if token:
        submission.set_pull_token(token)
    submission.full_clean(exclude=["attempt"])
    submission.save()
    return submission


def record_status(submission, status, *, detail="", scan_report=None, logs_excerpt=None):
    """The single write path for the four checks.

    Used by the organiser screen and by the range's ingest endpoint, so
    what a human sets and what a machine reports cannot drift apart.
    """
    from deployments.models import AppSubmission

    if status not in AppSubmission.Status.values:
        raise ValueError(f"Unknown status {status!r}.")

    submission.status = status
    submission.status_detail = detail or ""
    submission.status_changed_at = timezone.now()
    if scan_report is not None:
        submission.scan_report = scan_report
    if logs_excerpt is not None:
        submission.logs_excerpt = logs_excerpt
    submission.save(update_fields=[
        "status", "status_detail", "status_changed_at", "scan_report", "logs_excerpt",
    ])
    return submission


@transaction.atomic
def freeze(competition, *, now=None):
    """Select each team's last passing image for the range to pin.

    Returns {"pinned": [...], "missing": [...]} -- `missing` is the list
    that matters on the day: a team with no passing attempt at the freeze
    has no application in the competition, and somebody has to be told.
    """
    pinned, missing = [], []
    teams = (
        Team.objects.filter(
            competition=competition,
            team_type=Team.TeamType.BLUE,
            track=Track.APPLICATION,
        )
        .exclude(status=Team.Status.WITHDRAWN)
        .order_by("enterprise", "cell_id")
    )
    for team in teams:
        passing = last_passing(team)
        if passing is None:
            missing.append(team)
            continue
        pinned.append(passing)
    return {"pinned": pinned, "missing": missing}


def summary(competition):
    """Counts for the organiser screen and the live-day preflight."""
    from deployments.models import AppSubmission

    teams = Team.objects.filter(
        competition=competition, team_type=Team.TeamType.BLUE, track=Track.APPLICATION
    ).exclude(status=Team.Status.WITHDRAWN)
    passing = set(
        AppSubmission.objects.filter(
            competition=competition, status=AppSubmission.Status.HEALTHY
        ).values_list("team_id", flat=True)
    )
    submitted = set(
        AppSubmission.objects.filter(competition=competition).values_list("team_id", flat=True)
    )
    team_ids = set(teams.values_list("id", flat=True))
    return {
        "teams": len(team_ids),
        "passing": len(team_ids & passing),
        "submitted_not_passing": len((team_ids & submitted) - passing),
        "never_submitted": len(team_ids - submitted),
        "frozen": AppSubmission.objects.filter(
            competition=competition, frozen_at__isnull=False
        ).count(),
    }
