"""Ask everyone whose ID upload was lost to take it again.

The file store and the database disagree: Postgres holds a row saying
the document is on file, the bucket has never held the bytes, because
the upload landed on a host disk that was wiped at the next deploy.

Left alone that disagreement is worse than a plain gap. The profile page
reports the document as present, the checklist counts it as done, and the
registration can be submitted with nothing behind it -- while the
organiser's download link fails. So the sweep does two things in order:

1. clears `identity_document`, which makes the app's own state true
   again. The item reappears on the profile, in `registration_blockers`
   and in the daily outstanding digest without any new screens;
2. emails the person so they do not have to notice by themselves.

Clearing a field is not reversible, so the run is a preview unless
--confirm is given.
"""
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Team, TeamMember, UserProfile
from services import notification_catalogue as cat
from services import notification_service as ns


class Command(BaseCommand):
    help = "Clear identity documents missing from storage and ask their owners to re-upload."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Apply the changes and send the mail. Without it, only report.",
        )
        parser.add_argument(
            "--no-email",
            action="store_true",
            help="Clear the stale rows but send nothing (announce it yourself).",
        )

    def handle(self, *args, **options):
        confirm = options["confirm"]
        profiles = (
            UserProfile.objects.exclude(identity_document="")
            .select_related("user")
            .order_by("user__username")
        )

        lost, kept = [], 0
        for profile in profiles:
            name = profile.identity_document.name
            try:
                present = default_storage.exists(name)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(
                    f"Cannot reach the store ({type(exc).__name__}: {exc}). "
                    f"Fix that first -- otherwise this would clear every document."
                ))
                return
            if present:
                kept += 1
            else:
                lost.append((profile, name))

        self.stdout.write(f"Documents on file: {kept + len(lost)}   present: {kept}   missing: {len(lost)}")
        if not lost:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        locked = self._locked_members({p.user_id for p, _ in lost})
        for profile, name in lost:
            flag = "  [LOCKED REGISTRATION]" if profile.user_id in locked else ""
            self.stdout.write(
                f"  {profile.user.username} <{profile.user.email or 'no email'}>"
                f"  {name}{flag}"
            )

        if locked:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"{len(locked)} of these are on a submitted or approved registration. "
                f"Clearing the document does not reopen it, so the checklist will not "
                f"chase them -- follow those up by hand."
            ))

        if not confirm:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                "Preview only. Re-run with --confirm to clear these rows"
                + ("" if options["no_email"] else " and send the request") + "."
            ))
            return

        sent = 0
        with transaction.atomic():
            for profile, _ in lost:
                profile.identity_document = ""
                profile.identity_uploaded_at = None
                profile.save(update_fields=["identity_document", "identity_uploaded_at"])
                if not options["no_email"] and profile.user.email:
                    if cat.identity_document_lost(profile.user) is not None:
                        sent += 1

        self.stdout.write(self.style.SUCCESS(
            f"Cleared {len(lost)} stale document row(s); requested {sent} re-upload(s)."
        ))
        if not options["no_email"]:
            self.stdout.write(
                "Mail is queued and delivered on commit. If any is still pending, run "
                "`python manage.py send_pending_emails`."
            )

    @staticmethod
    def _locked_members(user_ids):
        """Members whose registration is already submitted or approved."""
        return set(
            TeamMember.objects.filter(
                user_id__in=user_ids, team__status__in=Team.LOCKED_STATUSES
            ).values_list("user_id", flat=True)
        )
