"""Submission Management business logic (Module 4).

Keeps upload rules, versioning, permissions and deadline enforcement out
of the views, per the project architecture:
    Browser -> Templates -> Views -> Services -> Models -> DB
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Team
from audit.models import AuditLog
from competitions.models import Competition
from services.audit_service import record as audit
from submissions.signals import submission_finalized
from submissions.models import (
    DocumentImage,
    ParsedDocument,
    Submission,
    SubmissionFile,
    SubmissionType,
)

logger = logging.getLogger(__name__)

# Submission types (Entry Guide section 3). Three registration-phase
# proposal types, one per track that submits one; Application Red is
# registration-only and goes through challenges from 1 October instead.
APPLICATION_BLUE_PROPOSAL = "Application Blue Proposal"
AI_DEFENCE_PROPOSAL = "AI Defence Proposal"
AI_RED_PROPOSAL = "AI Red Proposal"

PROPOSAL_TYPE_FOR = {
    (Team.TeamType.BLUE, Team.Track.APPLICATION): APPLICATION_BLUE_PROPOSAL,
    (Team.TeamType.BLUE, Team.Track.AI): AI_DEFENCE_PROPOSAL,
    (Team.TeamType.RED, Team.Track.AI): AI_RED_PROPOSAL,
}
PROPOSAL_TYPES = tuple(PROPOSAL_TYPE_FOR.values())

BLUE_TEAM_TYPES = ("Blue Team Documentation", "Blue Team Report")
RED_TEAM_TYPES = ("Red Team Documentation", "Red Team Report")
SHARED_TYPES = ("Supporting Evidence",)


def proposal_type_name(team):
    """The proposal type this team submits, or None (Application Red)."""
    return PROPOSAL_TYPE_FOR.get((team.team_type, team.track))


def is_proposal(submission):
    return submission.submission_type.name in PROPOSAL_TYPES


# One team cannot fill the disk by re-uploading forever.
MAX_VERSIONS_PER_SUBMISSION = 20


def teams_for(user):
    """Teams a user belongs to — as captain, linked member, or by email."""
    if not user.is_authenticated:
        return Team.objects.none()
    query = Q(captain=user) | Q(members__user=user)
    if user.email:
        query |= Q(members__email__iexact=user.email)
    return Team.objects.filter(query).select_related("competition").distinct()


def open_competitions_for(user):
    """Competitions the user may submit to right now (wizard step 1).

    The window is the organisers' switch plus the published submission
    deadline (Entry Guide section 7) -- not the event dates. Proposals are
    written weeks before the event; `start_date`/`end_date` describe the
    days at Strathmore, and gating on them shut the window before it had
    opened. The deadline is enforced by the platform clock.
    """
    now = timezone.now()
    team_competition_ids = teams_for(user).values_list("competition_id", flat=True)
    return (
        Competition.objects.filter(
            id__in=team_competition_ids,
            status__in=(Competition.Status.OPEN, Competition.Status.IN_PROGRESS),
        )
        # An organiser closing submissions must actually stop uploads, not
        # just hide the buttons. Competitions with no settings row yet are
        # left in -- the default is open.
        .exclude(settings_row__submissions_open=False)
        # Past the published deadline the window is shut, full stop.
        .exclude(settings_row__submission_deadline__lt=now)
        .distinct()
        .order_by("start_date")
    )


def submission_types_for(team):
    """Document types this team is allowed to file."""
    side = BLUE_TEAM_TYPES if team.team_type == Team.TeamType.BLUE else RED_TEAM_TYPES
    allowed = SHARED_TYPES + side
    proposal = proposal_type_name(team)
    if proposal:
        allowed = (proposal,) + allowed
    return SubmissionType.objects.filter(name__in=allowed).order_by("name")


def can_view(user, submission):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return teams_for(user).filter(pk=submission.team_id).exists()


def can_upload(user, submission):
    """Team members may upload while the competition is open and the
    submission has not been locked by review -- or, after the hard close,
    when an organiser has accepted a platform-fault claim for it."""
    if not can_view(user, submission):
        return False
    if user.is_staff:
        return True
    if submission.status in (Submission.Status.UNDER_REVIEW, Submission.Status.COMPLETED):
        return False
    if submission.late_upload_open:
        return True
    return submission.competition_id in set(
        open_competitions_for(user).values_list("id", flat=True)
    )


def deadline_passed(submission, now=None):
    deadline = submission.competition.settings.submission_deadline
    return bool(deadline and (now or timezone.now()) > deadline)


class HardClose(ValueError):
    """Upload refused because the submission deadline has passed."""


def get_or_create_thread(team, competition, submission_type):
    """One submission thread per (team, competition, type); uploads become versions."""
    submission, _ = Submission.objects.get_or_create(
        team=team, competition=competition, submission_type=submission_type
    )
    return submission


@transaction.atomic
def add_version(submission, form, user):
    """Save an uploaded file as the next version of a submission.

    `form` is a bound, valid SubmissionFileForm. Returns the new
    SubmissionFile. The submission stays a draft until finalize().
    """
    submission = Submission.objects.select_for_update().get(pk=submission.pk)
    next_version = submission.current_version + 1
    if next_version > MAX_VERSIONS_PER_SUBMISSION:
        raise ValueError(
            f"This submission already has {MAX_VERSIONS_PER_SUBMISSION} versions, "
            "the maximum. Contact the organisers if you need another."
        )

    # Hard close (Guide section 7): the platform clock decides, and the
    # only way past it is an organiser accepting a platform-fault claim,
    # which buys exactly one upload.
    late_path = False
    if deadline_passed(submission):
        if not submission.late_upload_open:
            deadline = submission.competition.settings.submission_deadline
            raise HardClose(
                f"Entry Guide section 7: the submission window closed on "
                f"{timezone.localtime(deadline):%d %b %Y at %H:%M} (EAT). Late work is "
                f"only accepted where the organisers confirm a platform fault -- "
                f"email them with the time you tried to upload."
            )
        late_path = True

    submission_file = form.save(commit=False)
    submission_file.submission = submission
    submission_file.version = next_version
    form.populate_metadata(submission_file, uploaded_by=user)
    submission_file.save()

    # Checksum must be computed from the stored bytes, after save.
    submission_file.checksum = submission_file.compute_checksum()
    submission_file.save(update_fields=["checksum"])

    submission.current_version = next_version
    update = ["current_version", "updated_at"]
    if late_path:
        submission.late_upload_used_at = timezone.now()
        update.append("late_upload_used_at")
    submission.save(update_fields=update)

    audit(
        AuditLog.Action.FILE_UPLOADED,
        actor=user,
        target=submission,
        description=f"Uploaded {submission_file.filename} as v{next_version}",
        version=next_version,
        checksum=submission_file.checksum,
        size=submission_file.file_size,
    )
    return submission_file


def parse_file(submission_file, force=False):
    """Run the Document Processing Engine over an uploaded file.

    Always returns a ParsedDocument; never raises. A parse failure is
    recorded so the upload itself is never lost.
    """
    from services import parser_service

    parsed, created = ParsedDocument.objects.get_or_create(
        submission_file=submission_file
    )
    if not created and not force and parsed.status == ParsedDocument.Status.SUCCEEDED:
        return parsed

    parsed.parser_version = parser_service.PARSER_VERSION
    parsed.parsed_at = timezone.now()

    if not submission_file.is_parsable:
        parsed.status = ParsedDocument.Status.SKIPPED
        parsed.error_message = "Format is stored but not text-extractable (e.g. ZIP)."
        parsed.save()
        return parsed

    try:
        result = parser_service.parse_document(submission_file.file.path)
    except Exception as exc:  # parser errors must not break uploads
        parsed.status = ParsedDocument.Status.FAILED
        parsed.error_message = str(exc)[:2000]
        parsed.save()
        return parsed

    parsed.status = ParsedDocument.Status.SUCCEEDED
    parsed.title = result.get("title", "")[:300]
    parsed.text = result.get("text", "")
    parsed.content = result
    parsed.error_message = ""
    parsed.save()

    _store_figures(parsed, submission_file)
    run_structure_check(submission_file, parsed)
    return parsed


def run_structure_check(submission_file, parsed=None):
    """Structure check for proposals (Guide section 9). Never raises."""
    from submissions.models import ProposalCheck

    submission = submission_file.submission
    if not is_proposal(submission):
        return None
    parsed = parsed or getattr(submission_file, "parsed", None)
    check, _ = ProposalCheck.objects.get_or_create(submission_file=submission_file)
    if parsed is None or parsed.status != ParsedDocument.Status.SUCCEEDED:
        check.status = ProposalCheck.Status.NOT_CHECKED
        check.save()
        return check
    try:
        from services import document_rules

        page_texts = []
        try:
            submission_file.file.open("rb")
            page_texts = document_rules.inspect_pdf(submission_file.file.read())["page_texts"]
        except Exception:  # noqa: BLE001 - headings alone still give a result
            pass
        finally:
            submission_file.file.close()
        result = document_rules.structure_check(
            parsed.content, submission.submission_type.name, page_texts=page_texts
        )
    except Exception:  # noqa: BLE001 - the check must never break an upload
        logger.exception("Structure check failed for file %s", submission_file.pk)
        check.status = ProposalCheck.Status.NOT_CHECKED
        check.save()
        return check
    check.front_matter = result["front_matter"]
    check.sections = result["sections"]
    check.body_pages = document_rules.count_body_pages(page_texts) if page_texts else None
    check.status = (
        ProposalCheck.Status.INCOMPLETE if check.missing_front_matter else ProposalCheck.Status.OK
    )
    check.save()
    return check


def _store_figures(parsed, submission_file):
    """Persist the document's figures so judges can see the diagrams.

    Best-effort: a figure that will not save must never cost us the parse
    we already succeeded at.
    """
    import hashlib

    from django.core.files.base import ContentFile

    from services import parser_service

    parsed.images.all().delete()  # re-parse replaces the previous set
    for figure in parser_service.extract_images(submission_file.file.path):
        try:
            data = figure["data"]
            digest = hashlib.sha256(data).hexdigest()
            page = figure.get("page")
            name = (
                f"{submission_file.pk}-"
                f"{page or 0}-{figure['index']}.{figure['format']}"
            )
            image = DocumentImage(
                parsed_document=parsed,
                page=page,
                index=figure["index"],
                width=figure.get("width") or 0,
                height=figure.get("height") or 0,
                image_format=figure["format"][:10],
                sha256=digest,
            )
            image.image.save(name, ContentFile(data), save=False)
            image.save()
        except Exception:  # noqa: BLE001 — figures are a bonus, not the job
            logger.exception(
                "Could not store a figure for submission file %s", submission_file.pk
            )


@transaction.atomic
def finalize(submission, user):
    """Move a draft to submitted and confirm it to the team."""
    if submission.current_version < 1:
        raise ValueError("Upload a document before submitting.")

    # Structure check (Guide section 9): a proposal missing its cover,
    # declaration or AI-use statement is returned, not queued for review.
    check = submission.latest_check
    if check is not None and check.missing_front_matter:
        submission.status = Submission.Status.RETURNED_INCOMPLETE
        submission.submitted_at = timezone.now()
        submission.save(update_fields=["status", "submitted_at", "updated_at"])
        audit(
            AuditLog.Action.SUBMISSION_SUBMITTED,
            actor=user,
            target=submission,
            description=f"v{submission.current_version} returned incomplete: "
                        + ", ".join(check.missing_front_matter),
        )
        from services import notification_catalogue as cat

        transaction.on_commit(lambda: cat.submission_returned_incomplete(submission, check))
        return submission

    submission.status = Submission.Status.SUBMITTED
    submission.submitted_at = timezone.now()

    # After the hard close only the organiser-accepted path reaches here;
    # record that it was late so judges and the penalty table see it.
    deadline = submission.competition.settings.submission_deadline
    if deadline and submission.submitted_at > deadline:
        submission.is_late = True
        submission.late_by = submission.submitted_at - deadline
    submission.save(
        update_fields=[
            "status",
            "submitted_at",
            "is_late",
            "late_by",
            "updated_at",
        ]
    )

    # Confirming the submission is Module 12's job now: notifications/signals
    # listens for `submission_finalized` below and notifies the whole team
    # (not just the captain and the uploader), in-app and by email. The
    # inline bulk_create that used to live here duplicated it.

    audit(
        AuditLog.Action.SUBMISSION_SUBMITTED,
        actor=user,
        target=submission,
        description=(
            f"Finalised v{submission.current_version}"
            + (" (late)" if submission.is_late else "")
        ),
        late=submission.is_late,
    )

    # Handoff to the judging module (Modules 6-9, owned elsewhere): they
    # subscribe to this to create the Evaluation, attach the rubric and
    # queue the AI run. Nothing here knows or cares who listens.
    submission_finalized.send(sender=Submission, submission=submission, actor=user)
    return submission


def accept_late_upload(submission, staff_user, evidence_at, note=""):
    """Organiser-accepted platform-fault path (Guide section 7).

    `evidence_at` is the timestamp of the captain's email reporting the
    fault. Unlocks one upload after the hard close.
    """
    submission.late_accepted_by = staff_user
    submission.late_accepted_at = timezone.now()
    submission.late_evidence_at = evidence_at
    submission.late_accepted_note = note[:300]
    submission.late_upload_used_at = None
    submission.save(update_fields=[
        "late_accepted_by", "late_accepted_at", "late_evidence_at",
        "late_accepted_note", "late_upload_used_at", "updated_at",
    ])
    audit(
        AuditLog.Action.SUBMISSION_SUBMITTED,
        actor=staff_user,
        target=submission,
        description=f"Late upload accepted (captain's email at {evidence_at:%d %b %Y %H:%M}). {note}".strip(),
    )
    return submission


def apply_penalty(submission, breach, staff_user, note="", percent=None):
    """Record a breach from the schedule at the competition's enforcement
    level (or an explicit percentage) and refresh the evaluation totals."""
    from submissions.models import SubmissionPenalty

    level = submission.competition.settings.enforcement_level
    penalty = SubmissionPenalty.objects.create(
        submission=submission,
        breach=breach,
        percent=breach.percent_for(level) if percent is None else percent,
        applied_by=staff_user,
        note=note[:300],
    )
    for evaluation in submission.evaluations.all():
        evaluation.recalculate_total()
    return penalty


def visible_submissions(user):
    """Queryset of submissions a user may see."""
    queryset = Submission.objects.select_related(
        "team", "competition", "submission_type"
    )
    if user.is_staff:
        return queryset
    return queryset.filter(team__in=teams_for(user))
