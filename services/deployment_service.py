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
            f"Team {team.pk} needs a valid Kubernetes namespace and an enterprise and cell assignment."
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


class FreezeConflict(ValueError):
    """A freeze cannot replace the selected or already pinned application."""


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


@transaction.atomic
def record_status(submission, status, *, detail="", scan_report=None, logs_excerpt=None,
                  resolved_digest=None, extra_digests=None):
    """The single write path for the four checks.

    Used by the organiser screen and by the range's ingest endpoint, so
    what a human sets and what a machine reports cannot drift apart.
    """
    from deployments.models import AppSubmission

    if status not in AppSubmission.Status.values:
        raise ValueError(f"Unknown status {status!r}.")
    if resolved_digest is not None and not valid_digest(resolved_digest):
        raise ValueError("A verified digest must be sha256 followed by 64 lowercase hex digits.")
    if resolved_digest is not None and status == AppSubmission.Status.HEALTHY:
        current = AppSubmission.objects.select_for_update().get(pk=submission.pk)
        if current.frozen_at:
            verify_extra_digests(current, extra_digests)

    submission.status = status
    submission.status_detail = detail or ""
    submission.status_changed_at = timezone.now()
    if scan_report is not None:
        submission.scan_report = scan_report
    if logs_excerpt is not None:
        submission.logs_excerpt = logs_excerpt
    fields = ["status", "status_detail", "status_changed_at", "scan_report", "logs_excerpt"]
    if resolved_digest is not None:
        submission.resolved_digest = resolved_digest
        fields.append("resolved_digest")
        if status == AppSubmission.Status.HEALTHY:
            submission.digest_verified_at = timezone.now()
            submission.deployed_digest_verified_at = submission.digest_verified_at
            fields.extend(["digest_verified_at", "deployed_digest_verified_at"])
    submission.save(update_fields=fields)
    return submission


def valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def valid_pinned_reference(value):
    return (isinstance(value, str) and len(value) <= 500
            and re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", value) is not None)


def verify_extra_digests(submission, observed):
    """Extra containers must identify all frozen content before a heartbeat."""
    expected = submission.frozen_extra_images or []
    observed = [] if observed is None else observed
    if (not isinstance(expected, list) or len(expected) != len(submission.extra_images or [])
            or any(not valid_pinned_reference(value) for value in expected)
            or not isinstance(observed, list)
            or observed != [value.rsplit("@", 1)[1] for value in expected]):
        raise ValueError("Every frozen extra image requires a matching observed digest.")


@transaction.atomic
def record_freeze(submission, digest, extra_digests=None, *, now=None):
    """Record the registry copy without inventing a current workload observation.

    A repeated receipt is idempotent. A tag recorded by the old selection
    command can be replaced by an actual pinned copy, but verified content
    and an existing pinned registry reference cannot change.
    """
    from deployments.models import AppSubmission

    if not valid_pinned_reference(digest):
        raise ValueError("digest must be a registry image reference ending in @sha256:<64 hex>")
    extras = [] if extra_digests is None else extra_digests
    Team.objects.select_for_update().get(pk=submission.team_id)
    current = AppSubmission.objects.select_for_update().get(pk=submission.pk)
    if (not isinstance(extras, list)
            or len(extras) != len(current.extra_images or [])
            or any(not valid_pinned_reference(item) for item in extras)):
        raise ValueError("extra_digests must contain one pinned digest per submitted extra image")
    raw_digest = digest.rsplit("@", 1)[1]
    already_pinned = valid_pinned_reference(current.frozen_digest)
    if ((already_pinned and (current.frozen_digest != digest
                            or current.frozen_extra_images != extras))
            or (current.frozen_verified_digest and current.frozen_verified_digest != raw_digest)):
        raise FreezeConflict("submission is already frozen to another digest")
    if already_pinned and current.frozen_verified_digest == raw_digest and current.frozen_at:
        return current
    passing = last_passing(current.team)
    if not current.is_passing or passing is None or passing.pk != current.pk:
        raise FreezeConflict("submission is not the team's last passing attempt")
    others = AppSubmission.objects.filter(team_id=current.team_id, frozen_at__isnull=False).exclude(pk=current.pk)
    if any(valid_pinned_reference(value) for value in others.values_list("frozen_digest", flat=True)):
        raise FreezeConflict("team already has a frozen application")
    current.frozen_digest = digest
    current.frozen_extra_images = extras
    current.frozen_verified_digest = raw_digest
    # Old selection-only timestamps cannot backdate a newly copied artifact.
    current.frozen_at = (current.frozen_at if already_pinned else None) or now or timezone.now()
    fields = ["frozen_digest", "frozen_extra_images", "frozen_verified_digest", "frozen_at"]
    if extras and not already_pinned:
        # A pre-freeze primary-image check did not attest this frozen set.
        current.deployed_digest_verified_at = None
        fields.append("deployed_digest_verified_at")
    current.save(update_fields=fields)
    return current


@transaction.atomic
def record_artifact(submission, resolved_digest, extra_digests=None):
    """Refresh workload identity independently of application availability."""
    from deployments.models import AppSubmission

    if not valid_digest(resolved_digest):
        raise ValueError("Invalid verified digest.")
    current = AppSubmission.objects.select_for_update().get(pk=submission.pk)
    verify_extra_digests(current, extra_digests)
    fields = ["resolved_digest", "deployed_digest_verified_at"]
    if (not current.frozen_verified_digest and current.frozen_at
            and valid_pinned_reference(current.frozen_digest)):
        # Existing range freezes predate the scoring fields. The current
        # heartbeat verifies the existing pinned copy without backdating it.
        current.frozen_verified_digest = current.frozen_digest.rsplit("@", 1)[1]
        fields.append("frozen_verified_digest")
    if not current.frozen_verified_digest:
        raise ValueError("The application has no verified frozen artifact.")
    current.resolved_digest = resolved_digest
    current.deployed_digest_verified_at = timezone.now()
    current.save(update_fields=fields)
    return current


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
