"""Phase B -- the registration flow of Entry Guide section 6.

    python manage.py test accounts

Covers: email verification and the inert account; institutional-domain
recognition; profile completion and the ID upload rules; the age band;
the code of conduct gate; invitations needing a verified account; the
submit step (blockers, lock, receipt, resubmission after rejection); the
organiser's pre-populated rejection reasons; and the welcome pack.
"""
import datetime
import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from accounts.forms import ProfileForm, TeamDecisionForm
from accounts.institutions import recognise
from accounts.models import (
    EmailVerification,
    PolicyAcceptance,
    PolicyVersion,
    Team,
    TeamInvitation,
    TeamMember,
    UserProfile,
)
from competitions.models import Competition
from notifications.management.commands.send_outstanding_digest import outstanding_for_member
from notifications.management.commands.send_registration_reminders import outstanding_for
from notifications.models import Notification
from services import account_service, team_service

PASSWORD = "Str0ng-pass-word!"
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
SKILLS = "We build Django and Go services with OWASP-aligned controls, run Burp and " \
         "ZAP in CI, and have shipped two audited student systems. " * 2


def make_user(username, email=None, verified=True, complete=True, dob=None):
    user = User.objects.create_user(
        username=username, email=email or f"{username}@strathmore.edu",
        password=PASSWORD, first_name=username.title(), last_name="Tester",
    )
    profile = user.profile
    profile.institution = "Strathmore University"
    if verified:
        profile.email_verified_at = timezone.now()
    if complete:
        profile.date_of_birth = dob or datetime.date(2004, 5, 1)
        profile.phone = "+254712345678"
        profile.student_number = "SU-1"
        profile.programme = "BSc ICS"
        profile.repo_handle = username
        profile.identity_document.save(f"{username}.png", SimpleUploadedFile("id.png", TINY_PNG), save=False)
    profile.save()
    return user


def make_competition():
    comp = Competition.objects.create(
        name="JS26 test", start_date=datetime.date(2026, 10, 14),
        end_date=datetime.date(2026, 10, 17), status=Competition.Status.OPEN,
    )
    row = comp.settings
    row.registration_deadline = timezone.now() + timedelta(days=10)
    row.save()
    return comp


def make_team(comp, captain, name="Simba", team_type=Team.TeamType.RED):
    team = Team.objects.create(
        competition=comp, team_name=name, team_type=team_type,
        track=Team.Track.APPLICATION, captain=captain, institution="Strathmore University",
    )
    TeamMember.objects.create(
        team=team, user=captain, student_name=captain.get_full_name(), email=captain.email,
        role=TeamMember.MemberRole.CAPTAIN, is_security_specialist=True,
    )
    team_service.assign_platform_role(captain, team)
    return team


def publish_policy(version="1.0"):
    return account_service.publish_policy(
        PolicyVersion.objects.create(version=version, body="Be excellent.", summary="first")
    )


class CommitMixin:
    """Run the request/service call with `transaction.on_commit` callbacks
    executed, since TestCase never commits."""

    def committed(self):
        return self.captureOnCommitCallbacks(execute=True)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="http://testserver")
class EmailVerificationTests(TestCase):
    def test_register_creates_inert_account_and_sends_link(self):
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.post(reverse("accounts:register"), {
                "username": "neema", "first_name": "Neema", "last_name": "A",
                "email": "neema@strathmore.edu", "password1": PASSWORD, "password2": PASSWORD,
                "privacy_accepted": "on",
            })
        self.assertRedirects(resp, reverse("accounts:verify_pending"))
        user = User.objects.get(username="neema")
        self.assertFalse(user.profile.is_email_verified)
        self.assertEqual(user.profile.institution, "Strathmore University")
        self.assertFalse(user.profile.manual_verification_required)
        self.assertEqual(EmailVerification.objects.filter(user=user).count(), 1)
        # Email goes through the notification queue (drained by cron), so
        # the proof is the queued row carrying the link.
        queued = Notification.objects.get(user=user, type=Notification.Type.ACCOUNT_CREATED)
        self.assertTrue(queued.send_email)
        self.assertIn("/accounts/verify/", queued.link)
        # Inert: every team flow bounces to the verify page.
        self.assertRedirects(self.client.get(reverse("accounts:team_form")), reverse("accounts:verify_pending"))
        self.assertRedirects(self.client.get(reverse("accounts:choose_team")), reverse("accounts:verify_pending"))

    def test_unrecognised_domain_flags_manual_verification(self):
        self.client.post(reverse("accounts:register"), {
            "username": "jo", "first_name": "Jo", "last_name": "B",
            "email": "jo@gmail.com", "password1": PASSWORD, "password2": PASSWORD,
            "privacy_accepted": "on",
        })
        profile = User.objects.get(username="jo").profile
        self.assertTrue(profile.manual_verification_required)
        self.assertFalse(profile.is_institution_verified)
        account_service.mark_institution_verified(profile, by=None)
        self.assertTrue(profile.is_institution_verified)

    def test_recognise_domains(self):
        self.assertEqual(recognise("a@students.uonbi.ac.ke"), (True, "University of Nairobi"))
        self.assertEqual(recognise("a@unknown.ac.ke"), (True, ""))
        self.assertEqual(recognise("a@gmail.com"), (False, ""))

    def test_verify_link_activates_and_expired_link_refused(self):
        user = make_user("kev", verified=False, complete=False)
        verification = account_service.send_verification(user)
        with self.captureOnCommitCallbacks(execute=True):
            resp = self.client.get(reverse("accounts:verify_email", args=[verification.token]))
        self.assertRedirects(resp, reverse("accounts:choose_role"), fetch_redirect_response=False)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.is_email_verified)
        self.assertEqual(Notification.objects.filter(user=user, type=Notification.Type.EMAIL_VERIFIED).count(), 1)
        # Second use is refused.
        with self.assertRaises(account_service.VerificationError):
            account_service.confirm_verification(verification.token)
        # Expired token.
        other = make_user("late", verified=False, complete=False)
        stale = account_service.send_verification(other)
        EmailVerification.objects.filter(pk=stale.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(account_service.VerificationError):
            account_service.confirm_verification(stale.token)

    def test_newer_link_invalidates_older(self):
        user = make_user("kev", verified=False, complete=False)
        first = account_service.send_verification(user)
        account_service.send_verification(user)
        self.assertFalse(EmailVerification.objects.filter(pk=first.pk).exists())

    def test_staff_issued_roles_count_as_verified(self):
        judge = make_user("judge", verified=False, complete=False)
        judge.profile.role = UserProfile.Role.JUDGE
        judge.profile.save()
        self.assertTrue(judge.profile.is_email_verified)


class ProfileCompletionTests(TestCase):
    def setUp(self):
        self.user = make_user("amina", complete=False)

    def _form(self, files=None, **extra):
        data = {"first_name": "Amina", "last_name": "K", "email": self.user.email,
                "institution": "Strathmore University"}
        data.update(extra)
        return ProfileForm(data, files or {}, instance=self.user.profile)

    def test_identity_document_rules(self):
        too_big = SimpleUploadedFile("id.png", TINY_PNG + b"\0" * (5 * 1024 * 1024), content_type="image/png")
        self.assertIn("5 MB", str(self._form({"identity_document": too_big}).errors["identity_document"]))
        fake_pdf = SimpleUploadedFile("id.pdf", b"MZ not a pdf", content_type="application/pdf")
        self.assertIn("not a valid PDF", str(self._form({"identity_document": fake_pdf}).errors["identity_document"]))
        docx = SimpleUploadedFile("id.docx", b"PK\x03\x04", content_type="application/octet-stream")
        self.assertIn("PDF, JPG or PNG", str(self._form({"identity_document": docx}).errors["identity_document"]))
        good = SimpleUploadedFile("id.png", TINY_PNG, content_type="image/png")
        form = self._form({"identity_document": good})
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.identity_document)
        self.assertIsNotNone(self.user.profile.identity_uploaded_at)

    def test_phone_must_be_e164(self):
        self.assertIn("phone", self._form(phone="0712345678").errors)
        self.assertTrue(self._form(phone="+254712345678").is_valid())

    def test_email_change_resets_verification(self):
        form = self._form(email="amina@gmail.com")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        profile = UserProfile.objects.get(user=self.user)
        self.assertFalse(profile.is_email_verified)
        self.assertTrue(profile.manual_verification_required)

    def test_age_band_checked_at_registration_date(self):
        profile = self.user.profile
        on = datetime.date(2026, 10, 1)
        profile.date_of_birth = datetime.date(2008, 10, 2)  # 17 the day before turning 18
        self.assertFalse(profile.is_age_eligible(on))
        profile.date_of_birth = datetime.date(2008, 10, 1)  # exactly 18
        self.assertTrue(profile.is_age_eligible(on))
        profile.date_of_birth = datetime.date(2000, 10, 1)  # exactly 26
        self.assertFalse(profile.is_age_eligible(on))
        profile.date_of_birth = datetime.date(2000, 10, 2)  # still 25
        self.assertTrue(profile.is_age_eligible(on))


class ChecklistAndConductTests(CommitMixin, TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("wanjiru")
        self.team = make_team(self.comp, self.captain)
        self.member = self.team.members.get(user=self.captain)

    def test_complete_member_has_nothing_outstanding_without_policy(self):
        self.assertEqual(outstanding_for_member(self.member), [])

    def test_missing_items_are_named(self):
        raw = make_user("raw", verified=False, complete=False)
        member = TeamMember.objects.create(team=self.team, user=raw, student_name="Raw", email=raw.email)
        items = outstanding_for_member(member)
        joined = " ".join(items)
        for phrase in ("Verify your email", "date of birth", "student number", "programme",
                       "repository handle", "Upload your student or national ID"):
            self.assertIn(phrase, joined)
        self.assertNotIn("phone", joined)  # plain members need no phone

    def test_captain_and_deputy_need_phone(self):
        self.captain.profile.phone = ""
        self.captain.profile.save()
        self.assertIn("phone", " ".join(outstanding_for_member(self.member)))
        deputy_user = make_user("juma")
        deputy_user.profile.phone = ""
        deputy_user.profile.save()
        member, _ = team_service.add_member(self.team, deputy_user)
        self.assertEqual(outstanding_for_member(member), [])
        team_service.set_deputy(self.team, member)
        member.refresh_from_db()
        self.assertIn("deputy", " ".join(outstanding_for_member(member)))

    def test_age_outside_band_is_outstanding(self):
        self.captain.profile.date_of_birth = datetime.date(1999, 1, 1)
        self.captain.profile.save()
        self.assertIn("18 to 25", " ".join(outstanding_for_member(self.member)))

    def test_policy_gate_and_republish(self):
        v1 = publish_policy("1.0")
        self.assertIn("code of conduct", " ".join(outstanding_for_member(self.member)))
        account_service.accept_policy(self.captain, v1, ip_address="127.0.0.1")
        self.assertEqual(outstanding_for_member(self.member), [])
        # A new version needs accepting again and notifies competitors.
        with self.committed():
            publish_policy("1.1")
        self.assertIn("code of conduct", " ".join(outstanding_for_member(self.member)))
        self.assertTrue(
            Notification.objects.filter(user=self.captain, type=Notification.Type.POLICY_UPDATED).exists()
        )
        self.assertEqual(PolicyVersion.objects.filter(is_current=True).count(), 1)

    def test_conduct_view_records_acceptance(self):
        v1 = publish_policy("1.0")
        self.client.login(username="wanjiru", password=PASSWORD)
        resp = self.client.get(reverse("accounts:conduct"))
        self.assertContains(resp, "Be excellent.")
        self.client.post(reverse("accounts:conduct"), {"accept": "on"})
        self.assertTrue(PolicyAcceptance.objects.filter(user=self.captain, version=v1).exists())

    def test_team_outstanding_counts_open_invitations(self):
        team_service.invite(self.team, "new@strathmore.edu", self.captain)
        self.assertIn("invitation", " ".join(outstanding_for(self.team)))


class InvitationTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("wanjiru")
        self.team = make_team(self.comp, self.captain)

    def test_unverified_account_cannot_accept(self):
        invitee = make_user("kev", verified=False)
        invitation = team_service.invite(self.team, invitee.email, self.captain)
        with self.assertRaisesMessage(team_service.TeamRuleError, "Verify your email"):
            team_service.accept_invitation(invitation, invitee)
        invitee.profile.email_verified_at = timezone.now()
        invitee.profile.save()
        member = team_service.accept_invitation(invitation, invitee)
        self.assertEqual(member.team, self.team)

    def test_fifth_member_refused_reserve_allowed(self):
        for i in range(3):
            team_service.add_member(self.team, make_user(f"m{i}"))
        with self.assertRaisesMessage(team_service.TeamRuleError, "fifth"):
            team_service.add_member(self.team, make_user("m5"))
        member, _ = team_service.add_member(self.team, make_user("res"), as_reserve=True)
        self.assertEqual(member.role, TeamMember.MemberRole.RESERVE)


class SubmitRegistrationTests(CommitMixin, TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.policy = publish_policy("1.0")
        self.captain = make_user("wanjiru")
        self.team = make_team(self.comp, self.captain)
        self.members = [self.captain]
        # Four, not three: a roster is now exactly MIN_MEMBERS, so a
        # three-person team cannot be submitted at all.
        for name in ("juma", "zawadi", "amina"):
            user = make_user(name)
            team_service.add_member(self.team, user)
            self.members.append(user)
        for user in self.members:
            account_service.accept_policy(user, self.policy)

    def submit(self, **overrides):
        kwargs = dict(track_preferences=["application", "ai"], enterprise_preference="a",
                      skills_declaration=SKILLS, mentor={"name": "Dr M", "email": "m@strathmore.edu"})
        kwargs.update(overrides)
        with self.committed():
            return team_service.submit_registration(self.team, **kwargs)

    def test_blockers_refuse_submission(self):
        self.captain.profile.identity_document = ""
        self.captain.profile.save()
        with self.assertRaisesMessage(team_service.TeamRuleError, "not complete"):
            self.submit()
        self.assertEqual(Team.objects.get(pk=self.team.pk).status, Team.Status.REGISTERED)

    def test_declaration_band_and_track_ranking(self):
        with self.assertRaisesMessage(team_service.TeamRuleError, "100 to 1500"):
            self.submit(skills_declaration="too short")
        with self.assertRaisesMessage(team_service.TeamRuleError, "Rank the tracks"):
            self.submit(track_preferences=["application", "application"])

    def test_submit_locks_and_sends_receipt(self):
        self.submit()
        team = Team.objects.get(pk=self.team.pk)
        self.assertEqual(team.status, Team.Status.SUBMITTED)
        self.assertIsNotNone(team.submitted_at)
        self.assertTrue(team.is_locked)
        self.assertEqual(team.track_preferences, ["application", "ai"])
        self.assertEqual(team.mentor_name, "Dr M")
        receipts = Notification.objects.filter(type=Notification.Type.REGISTRATION_SUBMITTED)
        # Derived, so the count follows the roster rule rather than
        # needing an edit every time the team size changes.
        self.assertEqual(receipts.count(), len(self.members))
        self.assertIn("Application Red, AI Red", receipts.first().body)
        # Locked: no invitations, no removals, no resubmission.
        with self.assertRaisesMessage(team_service.TeamRuleError, "submitted for review"):
            team_service.invite(team, "x@strathmore.edu", self.captain)
        with self.assertRaisesMessage(team_service.TeamRuleError, "submitted for review"):
            team_service.remove_member(team, team.members.exclude(user=self.captain).first())
        with self.assertRaises(team_service.TeamRuleError):
            team_service.submit_registration(team, track_preferences=["application"],
                                             enterprise_preference="", skills_declaration=SKILLS)

    def test_rejection_reopens_and_resubmission_gets_new_receipt(self):
        self.submit()
        team = Team.objects.get(pk=self.team.pk)
        team.status = Team.Status.REJECTED
        team.rejection_reason = "No member is identified as the team's security specialist."
        with self.committed():
            team.save()
        self.assertTrue(team.can_submit)
        self.assertFalse(team.is_locked)
        rejected = Notification.objects.get(user=self.captain, type=Notification.Type.REGISTRATION_REJECTED)
        self.assertIn("security specialist", rejected.body)
        self.team.refresh_from_db()
        self.submit()
        self.assertEqual(
            Notification.objects.filter(user=self.captain, type=Notification.Type.REGISTRATION_SUBMITTED).count(), 2
        )
        self.assertEqual(Team.objects.get(pk=team.pk).rejection_reason, "")

    def test_creating_a_team_no_longer_sends_a_receipt(self):
        self.assertFalse(Notification.objects.filter(type=Notification.Type.REGISTRATION_SUBMITTED).exists())

    def test_approval_carries_welcome_pack(self):
        self.submit()
        team = Team.objects.get(pk=self.team.pk)
        team.repository_url = "https://git.jengasec.org/js26/simba"
        team.namespace = "js26-simba"
        team_service.assign_identifier(team)
        team.status = Team.Status.APPROVED
        with self.committed():
            team.save()
        approved = Notification.objects.get(user=self.captain, type=Notification.Type.REGISTRATION_APPROVED)
        self.assertIn("Repository: https://git.jengasec.org/js26/simba", approved.body)
        self.assertIn("Namespace: js26-simba", approved.body)
        self.assertIn("JS26-R-001", approved.body)

    def test_submit_view_end_to_end(self):
        self.client.login(username="wanjiru", password=PASSWORD)
        resp = self.client.get(reverse("accounts:team_requests"))
        self.assertContains(resp, "Everything is in place")
        with self.committed():
            resp = self.client.post(reverse("accounts:registration_submit"), {
                "first_track": "application", "second_track": "ai", "enterprise_preference": "",
                "skills_declaration": SKILLS, "confirm": "on",
            })
        self.assertRedirects(resp, reverse("accounts:team_requests"))
        self.assertEqual(Team.objects.get(pk=self.team.pk).status, Team.Status.SUBMITTED)
        resp = self.client.get(reverse("accounts:team_requests"))
        self.assertContains(resp, "Under review")
        self.assertNotContains(resp, "Send invitation")


class OrganiserDecisionTests(TestCase):
    def test_rejection_reasons_are_prepopulated(self):
        form = TeamDecisionForm({"decision": "rejected", "reasons": ["no_specialist", "age"],
                                 "rejection_reason": "Juma's ID is unreadable."})
        self.assertTrue(form.is_valid(), form.errors)
        text = form.cleaned_data["rejection_reason"]
        self.assertIn("security specialist", text)
        self.assertIn("18-25", text)
        self.assertTrue(text.endswith("Juma's ID is unreadable."))
        empty = TeamDecisionForm({"decision": "rejected"})
        self.assertFalse(empty.is_valid())

    def test_staff_can_open_identity_document_and_others_cannot(self):
        comp = make_competition()
        captain = make_user("wanjiru")
        make_team(comp, captain)
        staff = User.objects.create_superuser("admin", "admin@strathmore.edu", PASSWORD)
        url = reverse("accounts:identity_document", args=[captain.pk])
        self.client.login(username="wanjiru", password=PASSWORD)
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.client.login(username="admin", password=PASSWORD)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content)[:8], TINY_PNG[:8])
        self.assertTrue(staff.is_staff)


class BriefPlacementTests(TestCase):
    """A brief is one application, and the enterprise is decided at
    selection -- not while teams are still choosing what to propose."""

    def setUp(self):
        from competitions.models import ApplicationBrief

        self.comp = make_competition()
        self.brief = ApplicationBrief.objects.create(
            competition=self.comp, code="APP03", name="Identity Service", enterprise=""
        )

    def blue_team(self, name, user):
        team = Team.objects.create(
            competition=self.comp, team_name=name, team_type=Team.TeamType.BLUE,
            track=Team.Track.APPLICATION, application_brief=self.brief, captain=user,
        )
        TeamMember.objects.create(
            team=team, user=user, student_name=user.get_full_name(),
            email=user.email, role=TeamMember.MemberRole.CAPTAIN,
        )
        return team

    def test_catalogue_lists_each_application_once(self):
        from services import competition_service

        team = self.blue_team("First", make_user("one"))
        codes = [b.code for b in competition_service.briefs_for(team)]
        self.assertEqual(codes, ["APP03"])
        # ...and still once after the team has been placed in an enterprise
        team.enterprise = "a"
        team.save()
        self.assertEqual([b.code for b in competition_service.briefs_for(team)], ["APP03"])

    def test_same_brief_is_built_once_per_enterprise(self):
        first = self.blue_team("First", make_user("one"))
        second = self.blue_team("Second", make_user("two"))
        third = self.blue_team("Third", make_user("three"))

        team_service.assign_cell(first)
        team_service.assign_cell(second)
        self.assertEqual(first.cell_id, "APP03")
        self.assertEqual(second.cell_id, "APP03")
        # Two builders, one per instance -- never the same one twice.
        self.assertNotEqual(first.enterprise, second.enterprise)
        self.assertEqual({first.enterprise, second.enterprise}, {"a", "b"})

        with self.assertRaisesMessage(team_service.TeamRuleError, "both enterprises"):
            team_service.assign_cell(third)

    def test_pinned_brief_is_honoured_while_that_side_is_free(self):
        self.brief.enterprise = "b"
        self.brief.save()
        team = self.blue_team("First", make_user("one"))
        team_service.assign_cell(team)
        self.assertEqual(team.enterprise, "b")

    def test_placement_balances_the_two_instances(self):
        from competitions.models import ApplicationBrief

        # Load ENTA up with other teams, then place a team whose brief is
        # free on both sides: it should go to the lighter instance.
        for i in range(3):
            Team.objects.create(
                competition=self.comp, team_name=f"Filler {i}", team_type=Team.TeamType.BLUE,
                track=Team.Track.APPLICATION, enterprise="a", cell_id=f"APP0{i + 4}",
            )
        other = ApplicationBrief.objects.create(
            competition=self.comp, code="APP01", name="Portal", enterprise=""
        )
        team = self.blue_team("Balanced", make_user("bal"))
        team.application_brief = other
        team.save()
        team_service.assign_cell(team)
        self.assertEqual(team.enterprise, "b")


class RegistrationDeadlineTests(CommitMixin, TestCase):
    """The published deadline actually closes registration -- it used to
    drive only the reminder emails."""

    def setUp(self):
        self.comp = make_competition()
        self.policy = publish_policy("1.0")
        self.captain = make_user("wanjiru")

    def close_registration(self):
        row = self.comp.settings
        row.registration_deadline = timezone.now() - timedelta(minutes=5)
        row.save()
        self.comp.refresh_from_db()

    def test_open_while_inside_the_deadline(self):
        self.assertTrue(self.comp.registration_open)
        self.assertIsNone(self.comp.registration_closed_at)

    def test_closed_once_the_deadline_passes(self):
        self.close_registration()
        self.assertFalse(self.comp.registration_open)
        self.assertIsNotNone(self.comp.registration_closed_at)

    def test_team_cannot_be_created_after_the_deadline(self):
        from accounts.forms import TeamCreateForm

        self.close_registration()
        form = TeamCreateForm({
            "competition": self.comp.pk, "team_name": "Latecomers",
            "team_type": Team.TeamType.RED, "track": Team.Track.AI, "institution": "Strathmore",
        })
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaisesMessage(team_service.TeamRuleError, "closed on"):
            team_service.create_team_for_captain(form, self.captain)

    def test_registration_cannot_be_submitted_after_the_deadline(self):
        team = make_team(self.comp, self.captain)
        for name in ("juma", "zawadi"):
            team_service.add_member(team, make_user(name))
        for user in [self.captain] + list(User.objects.filter(username__in=("juma", "zawadi"))):
            account_service.accept_policy(user, self.policy)
        self.close_registration()
        team.competition.refresh_from_db()
        with self.assertRaisesMessage(team_service.TeamRuleError, "closed on"):
            team_service.submit_registration(
                team, track_preferences=["application"], enterprise_preference="",
                skills_declaration=SKILLS,
            )


class IdentityDocumentDeliveryTests(TestCase):
    """A row whose bytes are gone must explain itself, not return a 500.

    Uploads taken before the site moved onto object storage did not
    survive -- the host's disk is wiped on every deploy while the
    Postgres row is not -- so organisers open ID links that point at
    objects the bucket has never held.
    """

    def setUp(self):
        self.staff = User.objects.create_user("organiser", password="x", is_staff=True)
        self.competitor = make_user("subject")
        self.profile = self.competitor.profile
        self.profile.identity_document.save(
            "id.png", SimpleUploadedFile("id.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64),
            save=True,
        )
        self.client.force_login(self.staff)
        self.url = reverse("accounts:identity_document", kwargs={"pk": self.competitor.pk})

    def tearDown(self):
        self.profile.identity_document.delete(save=False)

    def test_present_document_is_streamed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        # Windows will not unlink a file whose handle is still open, and
        # a streaming response holds one until it is consumed.
        response.close()

    def test_missing_file_is_410_with_an_explanation(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(
            identity_document="identity/2026/not-in-the-bucket.png"
        )
        with self.assertLogs("services.stored_files", level="WARNING"):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 410)
        body = response.content.decode()
        self.assertIn("not-in-the-bucket.png", body)
        self.assertIn("upload it again", body)

    def test_blank_field_is_still_404(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(identity_document="")
        self.assertEqual(self.client.get(self.url).status_code, 404)


class IdentityReuploadSweepTests(TestCase):
    """Bringing the database back in line with what storage actually holds.

    A row claiming a document that is not there is worse than a blank
    one: the checklist counts it as done and the registration can be
    submitted with nothing behind it.
    """

    def setUp(self):
        self.user = make_user("mislaid")
        self.profile = self.user.profile
        self.profile.identity_document.save(
            "id.png", SimpleUploadedFile("id.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32),
            save=True,
        )

    def tearDown(self):
        if self.profile.identity_document:
            self.profile.identity_document.delete(save=False)

    def lose_the_file(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(
            identity_document="identity/2026/never-arrived.png"
        )

    def run_command(self, *args):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("request_identity_reupload", *args, stdout=out)
        return out.getvalue()

    def test_present_document_is_left_alone(self):
        output = self.run_command("--confirm")
        self.assertIn("Nothing to do", output)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.identity_document)

    def test_preview_changes_nothing(self):
        self.lose_the_file()
        output = self.run_command()
        self.assertIn("Preview only", output)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.identity_document.name, "identity/2026/never-arrived.png")
        self.assertFalse(Notification.objects.filter(user=self.user).exists())

    def test_confirm_clears_the_row_and_asks_for_a_new_upload(self):
        self.lose_the_file()
        self.run_command("--confirm")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.identity_document.name, "")
        self.assertIsNone(self.profile.identity_uploaded_at)

        note = Notification.objects.get(user=self.user)
        self.assertIn("re-upload", note.subject.lower())
        self.assertIn("not yours", note.body)

    def test_the_item_goes_back_on_the_checklist(self):
        from notifications.management.commands.send_outstanding_digest import (
            outstanding_for_member,
        )

        team = make_team(make_competition(), make_user("skipper"))
        member, _ = team_service.add_member(team, self.user)
        self.assertNotIn(
            "Upload your student or national ID (PDF, JPG or PNG, up to 5 MB).",
            outstanding_for_member(member),
        )

        self.lose_the_file()
        self.run_command("--confirm")
        member.refresh_from_db()
        self.assertIn(
            "Upload your student or national ID (PDF, JPG or PNG, up to 5 MB).",
            outstanding_for_member(member),
        )

    def test_asking_twice_does_not_mail_twice(self):
        self.lose_the_file()
        self.run_command("--confirm")
        self.lose_the_file()
        self.run_command("--confirm")
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)


class StorageStatusPageTests(TestCase):
    """The upload-side view of storage, and who may see it."""

    def setUp(self):
        self.staff = User.objects.create_user("sysop", password="x", is_staff=True)
        self.url = reverse("accounts:storage_status")

    def test_staff_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(make_user("nosy"))
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_reports_the_backend_and_the_variable_names(self):
        self.client.force_login(self.staff)
        body = self.client.get(self.url).content.decode()
        self.assertIn("S3_BUCKET", body)
        self.assertIn("FileSystemStorage", body)

    def test_a_secret_value_is_never_rendered(self):
        self.client.force_login(self.staff)
        with override_settings():
            import os

            os.environ["S3_SECRET_ACCESS_KEY"] = "super-secret-value"
            try:
                body = self.client.get(self.url).content.decode()
            finally:
                del os.environ["S3_SECRET_ACCESS_KEY"]
        self.assertIn("S3_SECRET_ACCESS_KEY", body)
        self.assertNotIn("super-secret-value", body)

    def test_post_runs_a_round_trip(self):
        self.client.force_login(self.staff)
        body = self.client.post(self.url).content.decode()
        self.assertIn("Round trip OK", body)


class InvitationUrlRoutingTests(TestCase):
    """`invitations/revoke/<pk>/` must reach the revoke view.

    It used to be captured by `invitations/<token>/<decision>/` as
    token="revoke", decision="2", so withdrawing an invitation answered
    404 from a view that had no business running.
    """

    def test_revoke_resolves_to_the_revoke_view(self):
        match = resolve("/accounts/invitations/revoke/2/")
        self.assertEqual(match.view_name, "accounts:invitation_revoke")
        self.assertEqual(match.kwargs, {"pk": 2})

    def test_a_real_token_still_resolves(self):
        token = secrets.token_urlsafe(32)
        self.assertEqual(
            resolve(f"/accounts/invitations/{token}/").view_name,
            "accounts:invitation_open",
        )
        self.assertEqual(
            resolve(f"/accounts/invitations/{token}/accept/").view_name,
            "accounts:invitation_respond",
        )
        self.assertEqual(
            resolve(f"/accounts/verify/{token}/").view_name, "accounts:verify_email"
        )

    def test_a_word_is_no_longer_mistaken_for_a_token(self):
        with self.assertRaises(Resolver404):
            resolve("/accounts/invitations/revoke/")


class InvitationRevokeTests(TestCase):
    """The captain withdraws a pending invitation."""

    def setUp(self):
        self.captain = make_user("skipper")
        self.team = make_team(make_competition(), self.captain)
        self.invitation = team_service.invite(
            self.team, "recruit@strathmore.edu", self.captain
        )
        self.client.force_login(self.captain)
        self.url = reverse(
            "accounts:invitation_revoke", kwargs={"pk": self.invitation.pk}
        )

    def test_post_revokes_it(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, TeamInvitation.Status.REVOKED)
        self.assertIsNotNone(self.invitation.decided_at)

    def test_another_captain_cannot_revoke_it(self):
        other = make_user("interloper")
        make_team(self.team.competition, other, name="Nyati")
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, TeamInvitation.Status.PENDING)


class TeamReviewStateTests(TestCase):
    """The two organiser actions that are not approve or reject."""

    def setUp(self):
        self.staff = User.objects.create_user("organiser", password="x", is_staff=True)
        self.captain = make_user("skipper")
        self.comp = make_competition()
        self.team = make_team(self.comp, self.captain)
        self.team.status = Team.Status.SUBMITTED
        self.team.save()
        self.client.force_login(self.staff)
        self.url = reverse("accounts:team_decide", kwargs={"pk": self.team.pk})

    def decide(self, decision, **extra):
        return self.client.post(self.url, {"decision": decision, **extra})

    def test_under_review_locks_and_says_nothing_to_the_team(self):
        self.decide(Team.Status.UNDER_REVIEW)
        self.team.refresh_from_db()
        self.assertEqual(self.team.status, Team.Status.UNDER_REVIEW)
        self.assertTrue(self.team.is_locked)
        self.assertFalse(Notification.objects.filter(user=self.captain).exists())

    def test_a_team_under_review_cannot_change_its_roster(self):
        self.decide(Team.Status.UNDER_REVIEW)
        self.team.refresh_from_db()
        with self.assertRaises(team_service.TeamRuleError):
            team_service.check_unlocked(self.team, "invite anyone")

    def test_returning_unlocks_and_tells_the_captain_what_to_change(self):
        self.decide(Team.Status.REGISTERED, rejection_reason="The skills declaration is too short.")
        self.team.refresh_from_db()
        self.assertEqual(self.team.status, Team.Status.REGISTERED)
        self.assertFalse(self.team.is_locked)
        self.assertTrue(self.team.can_submit)

        note = Notification.objects.get(user=self.captain)
        self.assertIn("returned", note.subject)
        self.assertIn("too short", note.body)
        # A return is not a refusal, and must not read like one.
        self.assertNotIn("not accepted", note.subject)
        self.assertTrue(note.send_email)

    def test_returning_requires_a_reason(self):
        response = self.decide(Team.Status.REGISTERED)
        self.assertEqual(response.status_code, 200)  # redisplayed with the error
        self.team.refresh_from_db()
        self.assertEqual(self.team.status, Team.Status.SUBMITTED)

    def test_a_second_return_for_a_different_reason_is_delivered(self):
        """The dedupe key carries the note, so a new reason is not suppressed."""
        self.decide(Team.Status.REGISTERED, rejection_reason="Fix the roster.")
        self.team.status = Team.Status.SUBMITTED
        self.team.save()
        self.decide(Team.Status.REGISTERED, rejection_reason="Now fix the declaration.")
        self.assertEqual(Notification.objects.filter(user=self.captain).count(), 2)

    def test_the_return_dedupe_key_is_stable_across_processes(self):
        """It must not use built-in hash(), which is salted per process."""
        from services.notification_catalogue import _digest

        self.assertEqual(_digest("same text"), _digest("same text"))
        self.assertNotEqual(_digest("one"), _digest("two"))


class DataCompletionNoticeTests(CommitMixin, TestCase):
    """Chasing the person record, including on locked registrations."""

    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        self.team = make_team(self.comp, self.captain)
        # A member with a deliberate gap in their own record.
        self.gappy = make_user("gappy")
        team_service.add_member(self.team, self.gappy)
        UserProfile.objects.filter(user=self.gappy).update(programme="")

    def run_command(self, *args):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("send_data_completion_notice", *args, stdout=out)
        return out.getvalue()

    def test_preview_sends_nothing(self):
        output = self.run_command()
        self.assertIn("Preview only", output)
        self.assertFalse(Notification.objects.filter(user=self.gappy).exists())

    def test_confirm_asks_the_person_with_the_gap(self):
        self.run_command("--confirm")
        note = Notification.objects.get(user=self.gappy)
        self.assertIn("Finish setting up", note.subject)
        self.assertIn("programme", note.body)
        self.assertTrue(note.send_email)

    def test_the_notice_explains_the_basis_and_names_the_dpo(self):
        self.run_command("--confirm")
        body = Notification.objects.get(user=self.gappy).body
        self.assertIn("Data Protection Act 2019", body)
        self.assertIn("dpo@strathmore.edu", body)
        self.assertIn("gate at Strathmore", body)

    def test_a_locked_registration_is_still_chased(self):
        """Campus access depends on the record, and profiles are not locked."""
        self.team.status = Team.Status.SUBMITTED
        self.team.save()
        self.assertTrue(self.team.is_locked)
        self.run_command("--confirm")
        self.assertTrue(Notification.objects.filter(user=self.gappy).exists())

    def test_withdrawn_teams_are_left_alone(self):
        self.team.status = Team.Status.WITHDRAWN
        self.team.save()
        self.run_command("--confirm")
        self.assertFalse(Notification.objects.filter(user=self.gappy).exists())

    def test_asking_twice_does_not_mail_twice(self):
        self.run_command("--confirm")
        self.run_command("--confirm")
        self.assertEqual(Notification.objects.filter(user=self.gappy).count(), 1)

    def test_dry_run_overrides_confirm(self):
        self.run_command("--confirm", "--dry-run")
        self.assertFalse(Notification.objects.filter(user=self.gappy).exists())


class BriefingTests(CommitMixin, TestCase):
    """One email per competitor, assembled from their own record."""

    def setUp(self):
        self.comp = make_competition()
        self.captain = make_user("skipper")
        # A proposal-filing team, so the registration deadline applies.
        # Application Red is routed differently and is covered separately.
        self.team = make_team(self.comp, self.captain, team_type=Team.TeamType.BLUE)
        self.gappy = make_user("gappy")
        team_service.add_member(self.team, self.gappy)
        UserProfile.objects.filter(user=self.gappy).update(programme="")

    def run_command(self, *args):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("send_briefing", *args, stdout=out)
        return out.getvalue()

    def body_for(self, user):
        return Notification.objects.get(user=user).body

    def test_preview_sends_nothing_but_shows_a_sample(self):
        output = self.run_command()
        self.assertIn("as the first recipient would read it", output)
        self.assertIn("Preview only", output)
        self.assertFalse(Notification.objects.exists())

    def test_one_email_each_covering_dates_and_their_own_items(self):
        self.run_command("--confirm")
        body = self.body_for(self.gappy)
        self.assertIn("Registration closes", body)
        self.assertIn("programme", body)
        self.assertIn("dpo@strathmore.edu", body)
        # One message, not one per topic.
        self.assertEqual(Notification.objects.filter(user=self.gappy).count(), 1)

    def test_someone_with_nothing_outstanding_is_not_told_off(self):
        self.run_command("--confirm")
        body = self.body_for(self.captain)
        self.assertIn("nothing outstanding for you", body)
        self.assertIn("Registration closes", body)

    def test_a_competitor_who_never_uploaded_an_id_gets_no_apology(self):
        """The lost-file wording must not reach someone who never sent one."""
        self.run_command("--confirm")
        self.assertNotIn("our fault", self.body_for(self.gappy))

    def test_a_genuinely_lost_document_does_get_the_apology(self):
        UserProfile.objects.filter(user=self.gappy).update(
            identity_document="identity/2026/never-arrived.png"
        )
        self.run_command("--confirm")
        body = self.body_for(self.gappy)
        self.assertIn("our fault, not yours", body)
        # And the sweep cleared the dead reference, so the checklist chases it.
        self.gappy.profile.refresh_from_db()
        self.assertEqual(self.gappy.profile.identity_document.name, "")

    def test_an_extra_note_reaches_only_that_side(self):
        import tempfile, os

        # self.captain leads the blue team from setUp; this one leads a red one.
        red = make_user("redcap")
        make_team(self.comp, red, name="Nyati", team_type=Team.TeamType.RED)
        fd, path = tempfile.mkstemp(suffix=".md", text=True)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("RSVP on Meetup before Saturday.")
        try:
            self.run_command("--confirm", "--extra-red", path)
        finally:
            os.unlink(path)

        self.assertIn("RSVP on Meetup before Saturday", self.body_for(red))
        self.assertNotIn("RSVP on Meetup before Saturday", self.body_for(self.captain))

    def test_briefing_twice_with_the_same_slug_does_not_resend(self):
        self.run_command("--confirm")
        self.run_command("--confirm")
        self.assertEqual(Notification.objects.filter(user=self.gappy).count(), 1)

    def test_a_new_slug_sends_a_fresh_briefing(self):
        self.run_command("--confirm")
        self.run_command("--confirm", "--slug", "second-call")
        self.assertEqual(Notification.objects.filter(user=self.gappy).count(), 2)

    def test_locked_registrations_are_briefed_too(self):
        self.team.status = Team.Status.SUBMITTED
        self.team.save()
        self.run_command("--confirm")
        self.assertTrue(Notification.objects.filter(user=self.gappy).exists())

    def test_application_red_is_sent_at_the_qualifier_not_registration(self):
        """Their place is earned on Hack The Box, not by registering here.

        Quoting the platform registration deadline at them would send the
        team at the wrong thing entirely.
        """
        row = self.comp.settings
        row.red_rsvp_closes_at = timezone.now() + timedelta(days=3)
        row.red_qualifier_opens_at = timezone.now() + timedelta(days=4)
        row.red_qualifier_closes_at = timezone.now() + timedelta(days=5)
        row.save()

        hunter = make_user("hunter")
        make_team(self.comp, hunter, name="Nyati", team_type=Team.TeamType.RED)
        self.run_command("--confirm")

        body = self.body_for(hunter)
        self.assertIn("RSVP closes", body)
        self.assertIn("HTB qualifier opens", body)
        self.assertNotIn("Registration closes", body)
        self.assertNotIn("Proposals due", body)


class TeamlessCompetitorTests(CommitMixin, TestCase):
    """People who signed up and stopped.

    They hold no TeamMember row, so the digest, the closing reminder and
    the deadline chase -- all of which start from a team -- passed them
    by entirely. They are the people most likely to miss the deadline.
    """

    def setUp(self):
        self.comp = make_competition()
        self.policy = PolicyVersion.objects.create(
            version="9.0", title="Conduct", body="Text", is_current=True,
            published_at=timezone.now(),
        )
        self.drifter = make_user("drifter")
        UserProfile.objects.filter(user=self.drifter).update(role="")
        self.judge = make_user("beak")
        UserProfile.objects.filter(user=self.judge).update(role=UserProfile.Role.JUDGE)

    def run_briefing(self, *args):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command("send_briefing", *args, stdout=out)
        return out.getvalue()

    def test_a_teamless_competitor_is_briefed(self):
        output = self.run_briefing()
        self.assertIn("drifter", output)
        self.assertIn("NO TEAM", output)

    def test_they_are_told_to_join_a_team_first(self):
        self.run_briefing("--confirm")
        body = Notification.objects.get(user=self.drifter).body
        self.assertIn("Join a team", body)
        self.assertIn("Registration closes", body)

    def test_judges_are_not_chased_to_join_a_team(self):
        output = self.run_briefing()
        self.assertNotIn("beak", output)

    def test_the_policy_notice_reaches_a_competitor_with_no_role_yet(self):
        """Filtering on blue_team/red_team used to leave these people out."""
        version = PolicyVersion.objects.create(
            version="9.1", title="Conduct", body="New text",
        )
        with self.committed():
            account_service.publish_policy(version)
        self.assertTrue(
            Notification.objects.filter(
                user=self.drifter, type=Notification.Type.POLICY_UPDATED
            ).exists()
        )

    def test_the_policy_notice_does_not_go_to_judges(self):
        version = PolicyVersion.objects.create(version="9.2", title="C", body="T")
        with self.committed():
            account_service.publish_policy(version)
        self.assertFalse(
            Notification.objects.filter(
                user=self.judge, type=Notification.Type.POLICY_UPDATED
            ).exists()
        )

    def test_publishing_leaves_an_approved_team_approved(self):
        captain = make_user("skipper")
        team = make_team(self.comp, captain)
        team.status = Team.Status.APPROVED
        team.save()
        version = PolicyVersion.objects.create(version="9.3", title="C", body="T")
        with self.committed():
            account_service.publish_policy(version)
        team.refresh_from_db()
        self.assertEqual(team.status, Team.Status.APPROVED)
        # ...but everyone now shows as owing an acceptance.
        self.assertFalse(account_service.has_accepted_current_policy(captain))
