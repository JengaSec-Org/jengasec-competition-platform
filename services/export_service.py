"""Registration-phase exports (Module 10).

CSV, not PDF: during registration these are working documents — pasted
into a spreadsheet, filtered, mail-merged, handed to the finance desk for
the venue headcount. A PDF is the wrong shape for all of that, and the
scored-result PDFs belong to the judging phase.

Every row is written for a human reading a column, so the booleans are
Yes/No and the dates are the platform's own format, not ISO timestamps.
"""
import csv
import io

from django.utils import timezone

from accounts.models import Team, TeamInvitation, TeamMember
from competitions.constants import track_name
from notifications.management.commands.send_outstanding_digest import outstanding_for_member
from notifications.management.commands.send_registration_reminders import outstanding_for


def _writer():
    buffer = io.StringIO()
    # utf-8-sig so Excel on Windows opens the accented names correctly.
    return buffer, csv.writer(buffer)


def _stamp(value):
    return timezone.localtime(value).strftime("%d %b %Y %H:%M") if value else ""


def _yes(value):
    return "Yes" if value else "No"


def teams_csv(competition):
    """One row per team: the organisers' master registration list."""
    buffer, out = _writer()
    out.writerow([
        "Team identifier", "Team name", "Status", "Side", "Track",
        "Track preferences", "Brief", "Enterprise", "Cell", "Institution",
        "Captain", "Captain email", "Captain phone", "Members", "Reserve",
        "Security specialist", "Mentor", "Submitted at", "Outstanding items",
    ])
    teams = (
        Team.objects.filter(competition=competition)
        .select_related("captain", "captain__profile", "application_brief")
        .prefetch_related("members__user__profile")
        .order_by("team_identifier", "team_name")
    )
    for team in teams:
        members = list(team.members.all())
        playing = [m for m in members if m.role != TeamMember.MemberRole.RESERVE]
        reserve = next((m for m in members if m.role == TeamMember.MemberRole.RESERVE), None)
        specialists = [m.student_name for m in members if m.is_security_specialist]
        captain_profile = getattr(team.captain, "profile", None)
        out.writerow([
            team.team_identifier,
            team.team_name,
            team.get_status_display(),
            team.get_team_type_display(),
            team.track_label,
            ", ".join(team.track_preference_labels),
            team.application_brief.code if team.application_brief else "",
            team.enterprise_code,
            team.cell_id,
            team.institution,
            team.captain.get_full_name() if team.captain else "",
            team.captain.email if team.captain else "",
            captain_profile.phone if captain_profile else "",
            len(playing),
            reserve.student_name if reserve else "",
            ", ".join(specialists),
            team.mentor_name,
            _stamp(team.submitted_at),
            "; ".join(outstanding_for(team)),
        ])
    return buffer.getvalue()


def members_csv(competition):
    """One row per person: eligibility checking, and the venue headcount."""
    buffer, out = _writer()
    out.writerow([
        "Team identifier", "Team", "Name", "Email", "Role", "Security specialist",
        "Has account", "Email verified", "Institution confirmed", "Institution",
        "Date of birth", "Age at submission", "Student number", "Programme",
        "Year", "Phone", "Repo handle", "ID uploaded", "Conduct accepted",
        "Outstanding items",
    ])
    from services.account_service import has_accepted_current_policy

    members = (
        TeamMember.objects.filter(team__competition=competition)
        .select_related("team", "user", "user__profile")
        .order_by("team__team_identifier", "team__team_name", "-role", "student_name")
    )
    for member in members:
        profile = getattr(member.user, "profile", None)
        on_date = member.team.submitted_at.date() if member.team.submitted_at else None
        out.writerow([
            member.team.team_identifier,
            member.team.team_name,
            member.student_name,
            member.email,
            member.get_role_display(),
            _yes(member.is_security_specialist),
            _yes(member.user_id),
            _yes(profile and profile.is_email_verified),
            _yes(profile and profile.is_institution_verified),
            profile.institution if profile else "",
            profile.date_of_birth.strftime("%d %b %Y") if profile and profile.date_of_birth else "",
            profile.age_on(on_date) if profile and on_date and profile.date_of_birth else "",
            profile.student_number if profile else "",
            profile.programme if profile else "",
            profile.year_of_study if profile and profile.year_of_study else "",
            profile.phone if profile else "",
            profile.repo_handle if profile else "",
            _yes(profile and profile.identity_document),
            _yes(member.user_id and has_accepted_current_policy(member.user)),
            "; ".join(outstanding_for_member(member, on_date)),
        ])
    return buffer.getvalue()


def invitations_csv(competition):
    """Open and spent invitations -- who was asked and never came."""
    buffer, out = _writer()
    out.writerow(["Team", "Invited email", "Status", "Invited by", "Sent", "Expires", "Decided"])
    rows = (
        TeamInvitation.objects.filter(team__competition=competition)
        .select_related("team", "invited_by")
        .order_by("team__team_name", "-created_at")
    )
    for invitation in rows:
        out.writerow([
            invitation.team.team_name,
            invitation.email,
            invitation.get_status_display(),
            invitation.invited_by.get_full_name() if invitation.invited_by else "",
            _stamp(invitation.created_at),
            _stamp(invitation.expires_at),
            _stamp(invitation.decided_at),
        ])
    return buffer.getvalue()


def proposals_csv(competition):
    """One row per proposal: the selection working list."""
    from submissions.models import Submission

    buffer, out = _writer()
    out.writerow([
        "Team identifier", "Team", "Brief", "Application", "Type", "Status",
        "Selection", "Version", "Submitted at", "Late", "Penalties %",
        "Structure check", "Missing front matter",
    ])
    rows = (
        Submission.objects.filter(competition=competition)
        .select_related("team", "application_brief", "submission_type")
        .prefetch_related("penalties")
        .order_by("application_brief__code", "team__team_name")
    )
    for submission in rows:
        check = submission.latest_check
        out.writerow([
            submission.team.team_identifier,
            submission.team.team_name,
            submission.application_brief.code if submission.application_brief else "",
            submission.application_brief.name if submission.application_brief else "",
            submission.submission_type.name,
            submission.get_status_display(),
            submission.get_selection_status_display(),
            submission.current_version,
            _stamp(submission.submitted_at),
            _yes(submission.is_late),
            submission.penalty_percent,
            check.get_status_display() if check else "",
            "; ".join(check.missing_front_matter) if check else "",
        ])
    return buffer.getvalue()


EXPORTS = {
    "teams": ("Registrations by team", teams_csv),
    "members": ("People and eligibility", members_csv),
    "invitations": ("Invitations", invitations_csv),
    "proposals": ("Proposals and selection", proposals_csv),
}


def filename(competition, kind):
    date = timezone.localdate().strftime("%Y-%m-%d")
    slug = competition.name.lower().replace(" ", "-")
    return f"jengasec-{slug}-{kind}-{date}.csv"
