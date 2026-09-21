"""Phase B -- the registration flow of Entry Guide section 6.

    python manage.py test accounts

Covers: email verification and the inert account; institutional-domain
recognition; profile completion and the ID upload rules; the age band;
the code of conduct gate; invitations needing a verified account; the
submit step (blockers, lock, receipt, resubmission after rejection); the
organiser's pre-populated rejection reasons; and the welcome pack.
"""
import datetime
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
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
        for name in ("juma", "zawadi"):
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
        self.assertEqual(receipts.count(), 3)  # everyone on the team
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
