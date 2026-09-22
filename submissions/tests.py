"""Phase C -- submission rules of Entry Guide sections 7, 9 and 10.

    python manage.py test submissions

Covers: upload validation (type, size, encryption, extractable text, page
band, mandatory file name, ZIP only for evidence) with the Guide rule
quoted; the hard close and the organiser-accepted platform-fault path;
the structure check (front matter returns a proposal incomplete with a
notification; missing sections zero their criterion); penalties reducing
the weighted total at the enforcement level; feedback reports and the
72-hour procedural appeal with a second judge; reminder thresholds.
"""
import datetime
from datetime import timedelta
from decimal import Decimal

import fitz
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.models import Team, TeamMember, UserProfile
from competitions.models import Competition
from judging.models import Appeal, CriterionScore, Evaluation
from notifications.management.commands.send_deadline_reminders import THRESHOLDS_HOURS
from notifications.models import Notification
from rubrics.models import Rubric, RubricCriterion
from services import appeal_service, submission_service as svc, team_service
from services.document_rules import RuleViolation, structure_check
from submissions.forms import SubmissionFileForm
from submissions.models import PenaltySchedule, Submission, SubmissionType

PASSWORD = "Str0ng-pass-word!"

FRONT = ["Cover page", "Declaration of originality", "AI-use statement"]
BODY = [
    "1. Executive summary", "2. Problem statement", "3. Proposed solution", "4. Architecture",
    "5. Threat model", "6. Security controls", "7. Implementation plan", "8. Testing and validation",
    "9. Team and roles", "10. Timeline", "11. Risks and mitigations",
]
BLUE_EXTRA = ["12. API contract", "13. Data model", "14. Deployment configuration"]
TAIL = ["References"]


def make_pdf(headings, body_pages=None, password=None, blank=False, per_page=1):
    """A PDF with one heading per page (plus filler pages to reach
    `body_pages`), or blank pages to simulate a scan."""
    doc = fitz.open()
    pages = headings if body_pages is None else headings[:1] + headings[1:]
    for heading in pages:
        page = doc.new_page()
        if not blank:
            page.insert_text((72, 72), heading, fontsize=18)
            page.insert_text((72, 110), "Body text " * 40, fontsize=11)
    extra = 0 if body_pages is None else max(0, body_pages - (len(headings) - 2))
    for _ in range(extra):
        page = doc.new_page()
        page.insert_text((72, 72), "Continued " * 60, fontsize=11)
    kwargs = {}
    if password:
        kwargs = {"encryption": fitz.PDF_ENCRYPT_AES_256, "user_pw": password, "owner_pw": password}
    data = doc.tobytes(**kwargs)
    doc.close()
    return data


def good_blue_pdf(missing=()):
    headings = [h for h in FRONT + BODY + BLUE_EXTRA + TAIL if h not in missing]
    return make_pdf(headings)


class Base(TestCase):
    def setUp(self):
        self.comp = Competition.objects.create(
            name="JS26 test", start_date=datetime.date(2026, 10, 14),
            end_date=datetime.date(2026, 10, 17), status=Competition.Status.OPEN,
        )
        row = self.comp.settings
        row.submission_deadline = timezone.now() + timedelta(days=5)
        row.save()
        self.captain = User.objects.create_user("amina", "amina@strathmore.edu", PASSWORD)
        self.captain.profile.email_verified_at = timezone.now()
        self.captain.profile.save()
        self.team = Team.objects.create(
            competition=self.comp, team_name="Nyati", team_type=Team.TeamType.BLUE,
            track=Team.Track.APPLICATION, captain=self.captain, team_identifier="JS26-B-001",
            status=Team.Status.APPROVED,
        )
        TeamMember.objects.create(team=self.team, user=self.captain, student_name="Amina",
                                  email=self.captain.email, role=TeamMember.MemberRole.CAPTAIN)
        team_service.assign_platform_role(self.captain, self.team)
        self.stype = SubmissionType.objects.get(name=svc.APPLICATION_BLUE_PROPOSAL)
        self.submission = svc.get_or_create_thread(self.team, self.comp, self.stype)
        self.staff = User.objects.create_superuser("admin", "admin@strathmore.edu", PASSWORD)

    def expected_name(self, version=1):
        return f"JS26_TBA_TBA_JS26-B-001_APPBLUE_v{version}.pdf"

    def form(self, data, name=None, submission=None):
        upload = SimpleUploadedFile(name or self.expected_name(), data, content_type="application/pdf")
        return SubmissionFileForm({}, {"file": upload}, submission=submission or self.submission)

    def upload(self, data, name=None):
        form = self.form(data, name)
        self.assertTrue(form.is_valid(), form.errors)
        with self.captureOnCommitCallbacks(execute=True):
            uploaded = svc.add_version(self.submission, form, self.captain)
        svc.parse_file(uploaded)
        self.submission.refresh_from_db()
        return uploaded


class UploadValidationTests(Base):
    def error(self, data, name=None):
        form = self.form(data, name)
        self.assertFalse(form.is_valid())
        return str(form.errors["file"])

    def test_good_proposal_accepted(self):
        self.assertTrue(self.form(good_blue_pdf()).is_valid())

    def test_scanned_pdf_refused(self):
        msg = self.error(make_pdf(FRONT + BODY + BLUE_EXTRA + TAIL, blank=True))
        self.assertIn("Entry Guide section 7", msg)
        self.assertIn("extractable text", msg)

    def test_protected_pdf_refused(self):
        msg = self.error(make_pdf(FRONT + BODY + BLUE_EXTRA + TAIL, password="secret"))
        self.assertIn("password-protected", msg)

    def test_25_page_blue_proposal_refused(self):
        msg = self.error(make_pdf(FRONT + BODY + BLUE_EXTRA + TAIL, body_pages=25))
        self.assertIn("Entry Guide section 9", msg)
        self.assertIn("8 to 15 body pages", msg)

    def test_wrongly_named_file_refused(self):
        msg = self.error(good_blue_pdf(), name="proposal-final-v2.pdf")
        self.assertIn("named exactly JS26_TBA_TBA_JS26-B-001_APPBLUE_v1.pdf", msg)

    def test_name_follows_cell_and_version(self):
        self.team.enterprise, self.team.cell_id = "a", "APP03"
        self.team.save()
        self.submission.current_version = 1
        self.submission.save()
        self.assertTrue(self.form(good_blue_pdf(), name="JS26_ENTA_APP03_JS26-B-001_APPBLUE_v2.pdf").is_valid())
        self.assertIn("v2.pdf", self.error(good_blue_pdf(), name="JS26_ENTA_APP03_JS26-B-001_APPBLUE_v1.pdf"))

    def test_docx_proposal_refused(self):
        msg = self.error(b"PK\x03\x04docx", name="JS26_TBA_TBA_JS26-B-001_APPBLUE_v1.docx")
        self.assertIn("PDF only", msg)

    def test_oversized_proposal_refused(self):
        big = good_blue_pdf() + b"\n%" + b"0" * (25 * 1024 * 1024)
        self.assertIn("25 MB", self.error(big))

    def test_evidence_must_be_zip(self):
        evidence = svc.get_or_create_thread(
            self.team, self.comp, SubmissionType.objects.get(name="Supporting Evidence")
        )
        form = self.form(good_blue_pdf(), name="evidence.pdf", submission=evidence)
        self.assertFalse(form.is_valid())
        self.assertIn("ZIP bundle only", str(form.errors["file"]))
        form = self.form(b"PK\x03\x04" + b"0" * 100, name="evidence.zip", submission=evidence)
        self.assertTrue(form.is_valid(), form.errors)

    def test_zip_refused_for_reports(self):
        report = svc.get_or_create_thread(
            self.team, self.comp, SubmissionType.objects.get(name="Blue Team Report")
        )
        form = self.form(b"PK\x03\x04" + b"0" * 100, name="report.zip", submission=report)
        self.assertFalse(form.is_valid())
        self.assertIn("only accepted as Supporting Evidence", str(form.errors["file"]))


class HardCloseTests(Base):
    def close_window(self):
        row = self.comp.settings
        row.submission_deadline = timezone.now() - timedelta(hours=1)
        row.save()

    def test_upload_after_deadline_refused(self):
        self.close_window()
        form = self.form(good_blue_pdf())
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaisesMessage(svc.HardClose, "Entry Guide section 7: the submission window closed"):
            svc.add_version(self.submission, form, self.captain)
        self.assertFalse(svc.can_upload(self.captain, self.submission))

    def test_platform_fault_path_allows_one_upload_and_marks_late(self):
        self.close_window()
        evidence_at = timezone.now() - timedelta(hours=2)
        svc.accept_late_upload(self.submission, self.staff, evidence_at, note="Upload timed out at 16:58")
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.late_upload_open)
        self.assertTrue(svc.can_upload(self.captain, self.submission))
        self.upload(good_blue_pdf())
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.late_upload_open)
        # The one upload is spent.
        form = self.form(good_blue_pdf(), name=self.expected_name(2))
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(svc.HardClose):
            svc.add_version(self.submission, form, self.captain)
        with self.captureOnCommitCallbacks(execute=True):
            svc.finalize(self.submission, self.captain)
        self.submission.refresh_from_db()
        self.assertTrue(self.submission.is_late)
        self.assertEqual(self.submission.status, Submission.Status.SUBMITTED)
        self.assertEqual(self.submission.late_evidence_at, evidence_at)


class StructureCheckTests(Base):
    def test_complete_proposal_passes(self):
        uploaded = self.upload(good_blue_pdf())
        check = uploaded.structure_check
        self.assertEqual(check.status, "ok")
        self.assertEqual(check.missing_front_matter, [])
        self.assertEqual(check.missing_sections, [])
        self.assertEqual(len(check.sections), 15)

    def test_missing_ai_use_statement_returns_incomplete_and_notifies(self):
        self.upload(good_blue_pdf(missing=("AI-use statement",)))
        with self.captureOnCommitCallbacks(execute=True):
            svc.finalize(self.submission, self.captain)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, Submission.Status.RETURNED_INCOMPLETE)
        note = Notification.objects.get(user=self.captain, subject__startswith="Returned incomplete")
        self.assertIn("AI-use statement", note.body)
        self.assertFalse(Notification.objects.filter(subject__startswith="Submission received").exists())
        # Still allowed to fix it.
        self.assertTrue(svc.can_upload(self.captain, self.submission))

    def test_missing_section_maps_to_criterion(self):
        result = structure_check(
            {"sections": [{"heading": h} for h in FRONT + BODY[:-1] + BLUE_EXTRA + TAIL]},
            svc.APPLICATION_BLUE_PROPOSAL,
        )
        missing = [s for s in result["sections"] if not s["present"]]
        self.assertEqual([s["name"] for s in missing], ["Risks and mitigations"])
        self.assertEqual(missing[0]["criterion"], "risk")

    def test_ai_red_has_twelve_plus_three(self):
        result = structure_check({"sections": []}, svc.AI_RED_PROPOSAL)
        self.assertEqual(len(result["sections"]), 15)
        self.assertIn("Attack plan", [s["name"] for s in result["sections"]])


class ScoringRulesTests(Base):
    def setUp(self):
        super().setUp()
        self.rubric = Rubric.objects.create(competition=self.comp, submission_type=self.stype, title="Blue rubric")
        self.c_arch = RubricCriterion.objects.create(rubric=self.rubric, criterion="Architecture quality",
                                                     weight=Decimal("0.5"), max_score=Decimal("10"), display_order=1)
        self.c_risk = RubricCriterion.objects.create(rubric=self.rubric, criterion="Risk management",
                                                     weight=Decimal("0.5"), max_score=Decimal("10"), display_order=2)
        self.judge = User.objects.create_user("judy", "judy@strathmore.edu", PASSWORD)
        self.judge.profile.role = UserProfile.Role.JUDGE
        self.judge.profile.save()

    def evaluate(self):
        ev = Evaluation.objects.create(submission=self.submission, judge=self.judge, rubric=self.rubric,
                                       status=Evaluation.Status.APPROVED)
        CriterionScore.objects.create(evaluation=ev, criterion=self.c_arch, final_score=Decimal("8"),
                                      comments="Clear diagrams", evidence="Fig. 3")
        CriterionScore.objects.create(evaluation=ev, criterion=self.c_risk, final_score=Decimal("6"))
        return ev

    def test_missing_section_scores_zero(self):
        self.upload(good_blue_pdf(missing=("11. Risks and mitigations",)))
        ev = self.evaluate()
        self.assertEqual(ev.recalculate_total(), Decimal("0.40"))  # only architecture counts

    def test_penalty_reduces_weighted_total_at_enforcement_level(self):
        self.upload(good_blue_pdf())
        ev = self.evaluate()
        self.assertEqual(ev.recalculate_total(), Decimal("0.70"))
        pages = PenaltySchedule.objects.get(code="PAGES")
        # The same competition instance the service reads (settings are
        # cached per instance).
        row = self.submission.competition.settings
        row.enforcement_level = "strict"
        row.save()
        penalty = svc.apply_penalty(self.submission, pages, self.staff, note="17 body pages")
        self.assertEqual(penalty.percent, 10)
        ev.refresh_from_db()
        self.assertEqual(ev.weighted_total, Decimal("0.63"))
        row.enforcement_level = "relaxed"
        row.save()
        self.assertEqual(svc.apply_penalty(self.submission, pages, self.staff).percent, 2)

    def test_feedback_and_appeal_window(self):
        self.upload(good_blue_pdf())
        ev = self.evaluate()
        ev.recalculate_total()
        # Hidden until results are published.
        self.assertEqual(appeal_service.feedback_report(self.submission), [])
        with self.assertRaisesMessage(appeal_service.AppealError, "not been published"):
            appeal_service.file_appeal(self.submission, self.captain, "arithmetic", "x" * 60)
        row = self.submission.competition.settings
        row.results_published = True
        row.save()
        self.assertIsNotNone(row.results_published_at)
        report = appeal_service.feedback_report(self.submission)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["rows"][0]["comments"], "Clear diagrams")
        # Procedural grounds only; 50-char minimum; captain only.
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.file_appeal(self.submission, self.captain, "we deserved more", "x" * 60)
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.file_appeal(self.submission, self.judge, "arithmetic", "x" * 60)
        appeal = appeal_service.file_appeal(self.submission, self.captain, "arithmetic", "The totals do not add up to the published weights." * 2)
        self.assertEqual(appeal.grounds, "arithmetic")
        # Second judge must not be the first.
        with self.assertRaisesMessage(appeal_service.AppealError, "scored this submission"):
            appeal_service.assign_second_judge(appeal, self.judge)
        other = User.objects.create_user("jane", "jane@strathmore.edu", PASSWORD)
        other.profile.role = UserProfile.Role.JUDGE
        other.profile.save()
        self.assertEqual(list(appeal_service.eligible_second_judges(self.submission)), [other])
        appeal_service.assign_second_judge(appeal, other)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, Appeal.Status.UNDER_REVIEW)
        # 72 hours later the window is shut.
        row.results_published_at = timezone.now() - timedelta(hours=73)
        row.save()
        appeal.status = Appeal.Status.REJECTED
        appeal.save()
        with self.assertRaisesMessage(appeal_service.AppealError, "appeals close 72 hours"):
            appeal_service.file_appeal(self.submission, self.captain, "arithmetic", "x" * 60)


class ReminderTests(TestCase):
    def test_thresholds_match_registration(self):
        self.assertEqual(THRESHOLDS_HOURS, (168, 48, 12))
