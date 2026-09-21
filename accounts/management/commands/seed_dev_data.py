"""Seed a development database with one competition and a cast of users.

    python manage.py seed_dev_data            # create (idempotent: re-runs update, never duplicate)
    python manage.py seed_dev_data --reset    # delete the seeded rows first

Everything is prefixed so it is recognisable and removable. Every account
uses the same password, printed at the end. **Never run this against a
production database** -- it refuses unless DEBUG is on.

What you get, and what each account is for:

  admin        organiser        Command Center, Users, Teams, Roles
  judge        judge            Insights, review queue, rubrics
  partner      partner          Insights (read-only)
  amina        blue captain     Nyati Defenders, Application Blue on APP03 (APPROVED, JS26-B-001)
  brian        blue member      on Nyati Defenders (security specialist)
  wanjiru      red captain      Simba Cell, Application Red -- assembled but NOT submitted:
                                everything is in place except her own conduct acceptance
  juma         red member       on Simba Cell (deputy, security specialist)
  zawadi       red member       on Simba Cell
  kev          competitor       no team yet; an invitation from Simba is waiting for him
  neema        competitor       registered but email NOT verified (inert account)
  fatuma       staff-issued     judge account with NO password yet (set-password link)

Every competitor has a complete profile (DOB in band, ID uploaded, repo
handle...) and a verified email unless noted. Code of conduct v1.0 is
published and accepted by everyone except wanjiru and neema.

Also: the nine JengaBank briefs in both enterprises, the three proposal
types' placeholder rubrics, and one selected proposal so a cell exists.
"""
import datetime
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from django.core.management import call_command

from django.core.files.base import ContentFile

from accounts.models import PolicyVersion, Team, TeamMember, UserProfile
from competitions.models import ApplicationBrief, Competition
from rubrics.models import Rubric
from services import account_service, submission_service, team_service
from submissions.models import SubmissionType

User = get_user_model()

PASSWORD = "JengaSec-dev-2026"
COMPETITION = "JengaSec 2026"

PEOPLE = [
    # username, first, last, email, role, is_staff
    ("admin", "Chris", "Kirimi", "admin@jengasec.test", UserProfile.Role.ADMIN, True),
    ("judge", "Judy", "Mwangi", "judge@jengasec.test", UserProfile.Role.JUDGE, False),
    ("partner", "Peter", "Otieno", "partner@jengasec.test", UserProfile.Role.PARTNER, False),
    ("amina", "Amina", "Kamau", "amina@jengasec.test", "", False),
    ("brian", "Brian", "Odhiambo", "brian@jengasec.test", "", False),
    ("wanjiru", "Wanjiru", "Njoroge", "wanjiru@jengasec.test", "", False),
    ("juma", "Juma", "Ochieng", "juma@jengasec.test", "", False),
    ("zawadi", "Zawadi", "Wambui", "zawadi@jengasec.test", "", False),
    ("kev", "Kevin", "Mutua", "kev@jengasec.test", "", False),
    ("neema", "Neema", "Achieng", "neema@jengasec.test", "", False),
]

# Accounts that have *not* verified their address (inert until they do).
UNVERIFIED = {"neema"}
# Competitors who have not yet accepted the code of conduct.
NOT_ACCEPTED = {"wanjiru", "neema"}

# The smallest valid PNG (1x1, transparent) -- stands in for a scanned ID.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

CONDUCT_V1 = """1. Scope. This code applies to every competitor, mentor and reserve for the whole of the JengaSec 2026 season, on the platform and at the venue.

2. Rules of engagement. Attack only the targets assigned to your team, inside the engagement window, through the access the organisers issue. Anything else -- other teams' cells, the platform itself, the organisers' infrastructure, the public internet -- is out of scope and is treated as an attack on the event.

3. Honesty. Every proposal, report and piece of evidence is your team's own work. Declare all use of AI tools in the AI-use statement. Do not share, sell or reuse credentials, and report any you come across by accident.

4. People. Treat other competitors, judges and organisers with respect. Harassment, intimidation and discrimination of any kind end a team's participation immediately.

5. Data. Treat any data you reach during the competition as confidential. Do not exfiltrate, publish or retain it beyond what the evidence rules require.

6. Decisions. Organisers' rulings on scope, scoring and conduct are final, subject only to the appeals process in the Entry Guide.

Breaches are penalised under the schedule in the Entry Guide, up to disqualification."""


class Command(BaseCommand):
    help = "Seed a dev database with a competition, teams and users of every role."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete seeded rows first.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to seed: DEBUG is off. This is for development databases only.")

        if options["reset"]:
            self.reset()

        with transaction.atomic():
            comp = self.competition()
            policy = self.policy()
            users = {p[0]: self.user(*p) for p in PEOPLE}
            for username, user in users.items():
                self.complete_profile(username, user, policy)
            self.teams(comp, users)
            self.issued_account()

        self.stdout.write(self.style.SUCCESS("\nSeeded. Sign in at /login/ with any of:"))
        labels = {"admin": "organiser", "judge": "judge", "partner": "partner",
                  "amina": "Application Blue captain (Nyati Defenders, APP03)",
                  "brian": "blue member (Nyati Defenders)",
                  "wanjiru": "Application Red captain (Simba Cell; accept the conduct, then submit)",
                  "juma": "red member, deputy (Simba Cell)",
                  "zawadi": "red member (Simba Cell)",
                  "kev": "no team; invitation from Simba waiting",
                  "neema": "email NOT verified -- inert account"}
        for username, *_ in PEOPLE:
            self.stdout.write(f"  {username:10} {PASSWORD:22} {labels[username]}")
        self.stdout.write(f"  {'fatuma':10} {'(no password yet)':22} judge -- set it via the emailed link "
                          f"(console/locmem email) or Users -> Edit -> Re-send")
        self.stdout.write("\nFlow to try: log in as kev -> accept the invitation; as wanjiru -> Code of Conduct -> accept -> My Team -> "
                          "Submit registration; as admin -> Teams -> Simba Cell -> approve (gets an APPRED cell). "
                          "Log in as neema to see the inert, unverified state.")

    # ------------------------------------------------------------------

    def reset(self):
        Team.objects.filter(competition__name=COMPETITION).delete()
        Competition.objects.filter(name=COMPETITION).delete()
        User.objects.filter(email__endswith="@jengasec.test").delete()
        PolicyVersion.objects.filter(version="1.0").delete()
        self.stdout.write("Seeded rows removed.")

    def policy(self):
        version, _ = PolicyVersion.objects.update_or_create(
            version="1.0",
            defaults={
                "title": "JengaSec Code of Conduct",
                "summary": "First published version for the JengaBank edition.",
                "body": CONDUCT_V1,
            },
        )
        if not version.is_current:
            PolicyVersion.objects.filter(is_current=True).update(is_current=False)
            version.is_current = True
            version.published_at = timezone.now()
            version.save(update_fields=["is_current", "published_at"])
        return version

    def complete_profile(self, username, user, policy):
        """Verified address, complete eligibility details, ID on file."""
        from services import account_service

        profile = user.profile
        profile.refresh_from_db()  # the cached instance predates user() setting the institution
        if username in UNVERIFIED:
            profile.email_verified_at = None
        elif profile.email_verified_at is None:
            profile.email_verified_at = timezone.now()
        # The @jengasec.test addresses are not institutional; treat them as
        # recognised so the seed does not need an organiser's confirmation.
        profile.manual_verification_required = False
        if profile.role in ("", UserProfile.Role.BLUE_TEAM, UserProfile.Role.RED_TEAM):
            profile.date_of_birth = datetime.date(2004, 3, 14)
            profile.phone = profile.phone or f"+2547{abs(hash(username)) % 10**8:08d}"
            profile.student_number = profile.student_number or f"SU-{abs(hash(username)) % 10**6:06d}"
            profile.programme = profile.programme or "BSc Informatics and Computer Science"
            profile.year_of_study = profile.year_of_study or 3
            profile.repo_handle = profile.repo_handle or username
            if not profile.identity_document:
                profile.identity_document.save(f"{username}-id.png", ContentFile(TINY_PNG), save=False)
                profile.identity_uploaded_at = timezone.now()
        profile.save()
        if username not in NOT_ACCEPTED:
            account_service.accept_policy(user, policy)

    def competition(self):
        now = timezone.now()
        comp, _ = Competition.objects.update_or_create(
            name=COMPETITION,
            defaults={
                "theme": "The JengaBank edition",
                "description": "Development seed. Two JengaBank enterprises; blue teams build one service each, red teams break them.",
                "start_date": datetime.date(2026, 10, 14),
                "end_date": datetime.date(2026, 10, 17),
                "status": Competition.Status.OPEN,
            },
        )
        row = comp.settings
        row.submissions_open = True
        row.registration_deadline = now + timedelta(days=5)
        row.build_deadline = datetime.datetime(2026, 10, 10, 17, 0, tzinfo=datetime.timezone.utc)
        row.submission_deadline = now + timedelta(days=45)
        row.save()
        call_command("seed_application_briefs", competition=comp.pk, stdout=self.stdout)
        # Rubrics are per submission type; one placeholder per proposal type.
        for name in submission_service.PROPOSAL_TYPES:
            stype = SubmissionType.objects.filter(name=name).first()
            if stype:
                Rubric.objects.get_or_create(
                    competition=comp, submission_type=stype,
                    defaults={"title": f"{name} rubric (placeholder)"},
                )
        return comp

    def user(self, username, first, last, email, role, is_staff):
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first, "last_name": last, "email": email},
        )
        user.first_name, user.last_name, user.email = first, last, email
        user.is_staff = is_staff
        user.is_superuser = is_staff
        user.set_password(PASSWORD)
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if role:
            profile.role = role
        profile.institution = "Strathmore University"
        profile.save()
        return user

    def teams(self, comp, u):
        app03 = ApplicationBrief.objects.get(competition=comp, enterprise="a", code="APP03")

        # Application Blue, registration approved and (via a selected
        # proposal) placed in ENTA APP03.
        nyati, _ = Team.objects.update_or_create(
            competition=comp, team_name="Nyati Defenders",
            defaults={
                "team_type": Team.TeamType.BLUE, "track": Team.Track.APPLICATION,
                "application_brief": app03, "application_choice": app03.name,
                "institution": "Strathmore University", "captain": u["amina"],
            },
        )
        TeamMember.objects.get_or_create(team=nyati, user=u["amina"], defaults={
            "student_name": "Amina Kamau", "email": u["amina"].email, "role": TeamMember.MemberRole.CAPTAIN})
        if not nyati.members.filter(user=u["brian"]).exists():
            team_service.add_member(nyati, u["brian"])
        nyati.members.filter(user=u["brian"]).update(is_security_specialist=True)
        team_service.assign_platform_role(u["amina"], nyati)
        team_service.assign_identifier(nyati)
        if nyati.status != Team.Status.APPROVED:
            nyati.status = Team.Status.APPROVED
            nyati.save()
        if not nyati.cell_id:
            team_service.assign_cell(nyati)

        # Application Red, still awaiting organiser review (no proposal for
        # this track; it is placed in an APPRED cell on approval).
        simba, _ = Team.objects.update_or_create(
            competition=comp, team_name="Simba Cell",
            defaults={
                "team_type": Team.TeamType.RED, "track": Team.Track.APPLICATION,
                "institution": "KCA University", "captain": u["wanjiru"],
            },
        )
        TeamMember.objects.get_or_create(team=simba, user=u["wanjiru"], defaults={
            "student_name": "Wanjiru Njoroge", "email": u["wanjiru"].email,
            "role": TeamMember.MemberRole.CAPTAIN, "is_security_specialist": True})
        team_service.assign_platform_role(u["wanjiru"], simba)
        for name in ("juma", "zawadi"):
            if not simba.members.filter(user=u[name]).exists():
                team_service.add_member(simba, u[name])
        simba.members.filter(user=u["juma"]).update(
            role=TeamMember.MemberRole.DEPUTY, is_security_specialist=True
        )

        # A competitor with no team and an invitation waiting on him. Simba
        # is still being assembled, so it can invite; approved Nyati cannot.
        if not simba.invitations.filter(email__iexact=u["kev"].email).exists() \
                and not simba.members.filter(user=u["kev"]).exists():
            team_service.invite(simba, u["kev"].email, u["wanjiru"])

    def issued_account(self):
        if User.objects.filter(username="fatuma").exists():
            return
        account_service.issue_account(
            username="fatuma", email="fatuma@jengasec.test", first_name="Fatuma", last_name="Hassan",
            role=UserProfile.Role.JUDGE, institution="Strathmore University",
        )
