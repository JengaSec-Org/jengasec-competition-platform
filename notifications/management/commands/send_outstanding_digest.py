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


def outstanding_for_member(member):
    """What this member personally still owes. Empty list means done."""
    missing = []
    if member.user is None:
        # Nothing to send in-app to, but recorded so the captain's view of
        # the team is honest about it.
        missing.append("Create a platform account using this email address.")
        return missing
    if not member.user.email:
        missing.append("Add an email address to your account.")
    if not member.user.first_name and not member.user.last_name:
        missing.append("Add your full name to your profile.")
    profile = getattr(member.user, "profile", None)
    if profile is not None and not profile.role:
        missing.append("Your account has no role yet -- ask an organiser to set it.")
    if profile is not None and not profile.institution:
        missing.append("Set your institution on your profile.")
    return missing


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
                team__status__in=(Team.Status.REGISTERED, Team.Status.APPROVED)
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
