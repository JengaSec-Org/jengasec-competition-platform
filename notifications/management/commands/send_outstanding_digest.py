"""Daily digest of what each team member still has to do.

    python manage.py send_outstanding_digest
    python manage.py send_outstanding_digest --dry-run

Run once a day from cron:

    0 7 * * * cd /srv/jengasec && .venv/bin/python manage.py send_outstanding_digest

**Suppressed once complete.** A member with nothing outstanding is not
mailed at all -- an empty daily digest is precisely what teaches people to
filter the sender away. The digest is deduplicated per member per day, so
running the command twice does not send twice.

`outstanding_for_member()` is the rule, and it is deliberately the only
place that defines "outstanding". It currently covers what the schema can
answer today. As the accounts and registration flows land -- verified
email, signed policy, accepted invitation -- extend this one function and
every caller improves with it. See docs/HANDOFF-notifications.md.
"""
from django.core.management.base import BaseCommand

from accounts.models import Team, TeamMember
from services import notification_catalogue as cat


def outstanding_for_user(user, on_date=None, contact_role=""):
    """What one person still owes on their own account.

    Entry Guide section 6, steps 1-7: verified email, recognised (or
    manually confirmed) institution, profile details, ID document within
    the age band, and the current code of conduct. `on_date` is the
    registration date the age rule is checked against; today when not
    given. `contact_role` adds the phone-number requirement that applies
    to a captain or deputy, and is blank for anyone else.

    Split out from `outstanding_for_member` so the same checklist can be
    applied to somebody who has no team yet -- an account that signed up
    and stopped has exactly these gaps plus a team to join, and was
    previously invisible to every reminder the platform sends.
    """
    missing = []
    if not user.email:
        missing.append("Add an email address to your account.")
    if not user.first_name and not user.last_name:
        missing.append("Add your full name to your profile.")
    profile = getattr(user, "profile", None)
    if profile is None:
        return missing
    if not profile.is_email_verified:
        missing.append("Verify your email address (the link is in your inbox).")
    if not profile.is_institution_verified:
        missing.append(
            "Your address is not at a recognised institution; an organiser "
            "will confirm your student status from your ID document."
        )
    if not profile.institution:
        missing.append("Set your institution on your profile.")
    if profile.date_of_birth is None:
        missing.append("Add your date of birth to your profile.")
    elif not profile.is_age_eligible(on_date):
        missing.append(
            f"Competitors must be {profile.MIN_AGE} to {profile.MAX_AGE} on the "
            f"registration date; your date of birth puts you outside that."
        )
    if not profile.student_number:
        missing.append("Add your student number to your profile.")
    if not profile.programme:
        missing.append("Add your programme of study to your profile.")
    if not profile.repo_handle:
        missing.append("Add your repository handle (GitHub / GitLab) to your profile.")
    if not profile.identity_document:
        missing.append("Upload your student or national ID (PDF, JPG or PNG, up to 5 MB).")
    if contact_role and not profile.phone:
        missing.append(
            f"As {contact_role} you must add a phone number in international format."
        )
    from services.account_service import has_accepted_current_policy

    if not has_accepted_current_policy(user):
        missing.append("Read and accept the current code of conduct.")
    return missing


def outstanding_for_member(member, on_date=None):
    """What this member personally still owes. Empty list means done.

    The account checklist, plus the one item that depends on where they
    stand in the team.
    """
    if member.user is None:
        # Nothing to send in-app to, but recorded so the captain's view of
        # the team is honest about it.
        return ["Create a platform account using this email address."]
    contact_role = (
        member.get_role_display().lower()
        if member.role in member.CONTACT_ROLES
        else ""
    )
    return outstanding_for_user(member.user, on_date=on_date, contact_role=contact_role)


class Command(BaseCommand):
    help = "Email each team member the items they still have outstanding."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent without sending it.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        sent = suppressed = 0

        members = (
            TeamMember.objects.filter(
                team__status__in=(
                    Team.Status.REGISTERED, Team.Status.REJECTED, Team.Status.APPROVED
                )
            )
            .select_related("team", "team__competition", "user", "user__profile")
        )

        for member in members:
            items = outstanding_for_member(member)
            if not items or member.user is None:
                suppressed += 1
                continue
            if dry_run:
                self.stdout.write(
                    f"{member.user.username} ({member.team.team_name}): "
                    f"{len(items)} item(s)"
                )
                sent += 1
                continue
            if cat.outstanding_items(member.user, member.team, items) is not None:
                sent += 1

        verb = "would notify" if dry_run else "notified"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {sent} member(s); {suppressed} had nothing outstanding."
            )
        )
