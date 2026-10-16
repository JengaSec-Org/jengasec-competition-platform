"""Appeals bind one result, require independent review and retain shared proof."""
import hashlib
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import TeamMember, UserProfile
from accounts.tests import make_competition, make_user
from jengasec_scoring_policy import canonical_json, digest
from judging.models import Appeal, Evaluation
from notifications.models import Notification
from rubrics.models import Rubric
from scoring.models import ScoreRevision, ScoringEvidence
from scoring.tests import blue_team
from services import appeal_service, scoring_service
from submissions.models import Submission, SubmissionType

REASON = "The published calculation used an incomplete observation window and did not apply the stated rule."
RESOLUTION = "The retained receipt timestamps confirm the procedural error described in this appeal."


class AppealWorkflowTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp)
        self.rival = blue_team(self.comp, "Other", "APP02")
        self.staff = User.objects.create_user("appeal-organizer", is_staff=True, password="x")
        self.first = self.judge("original-marker")
        self.second = self.judge("independent-reviewer")
        self.third = self.judge("other-reviewer")
        snapshot = {"criterion": "availability", "verified_count": 1}
        content = canonical_json({"snapshot": snapshot, "records": [{"id": "metric_test_probe"}]})
        self.archive = ScoringEvidence(
            competition=self.comp, team=self.team, family="metrics", snapshot=snapshot,
            sha256=hashlib.sha256(content).hexdigest(), input_sha256=digest(snapshot),
            byte_size=len(content), verified_at=timezone.now())
        self.archive.archive.save(f"appeal-{self.archive.pk}.json", ContentFile(content), save=False)
        self.archive.save()
        self.event = self.award("availability", 7)
        self.revision = self.event.revisions.get()
        settings = self.comp.settings
        settings.results_published = True
        settings.save()

    def judge(self, username):
        user = make_user(username)
        user.profile.role = UserProfile.Role.JUDGE
        user.profile.save()
        return user

    def award(self, criterion, value, judge=None):
        return scoring_service.award(
            self.team, "main", value, source="judge", criterion=criterion,
            dedupe_key=f"main:{criterion}", recorded_by=judge or self.first,
            metadata={"evidence": [{"id": str(self.archive.pk), "sha256": self.archive.sha256}]})

    def file(self, revision=None):
        return appeal_service.file_score_appeal(revision or self.revision, self.team.captain, "arithmetic", REASON)

    def assign(self, appeal):
        return appeal_service.assign_second_judge(appeal, self.second, by=self.staff)

    def submission(self):
        kind = SubmissionType.objects.first()
        submission = Submission.objects.create(competition=self.comp, team=self.team, submission_type=kind)
        rubric = Rubric.objects.create(competition=self.comp, submission_type=kind, title="Appeal test rubric")
        Evaluation.objects.create(submission=submission, rubric=rubric, judge=self.first,
                                  status=Evaluation.Status.APPROVED, weighted_total=7)
        return submission

    def test_captain_files_against_exact_revision_and_retains_evidence(self):
        appeal = self.file()
        self.assertEqual(appeal.score_revision_id, self.revision.pk)
        self.assertEqual(appeal.score_event_id, self.event.pk)
        self.assertIsNone(appeal.submission_id)
        self.assertEqual(list(appeal.retained_evidence.all()), [self.archive])
        self.archive.refresh_from_db()
        self.assertTrue(self.archive.appeal_hold)
        self.assertEqual(self.archive.retain_until, self.comp.settings.appeal_window_closes_at)

    def test_only_the_active_owner_captain_can_file(self):
        for user in (self.rival.captain, self.first, self.staff):
            with self.subTest(user=user.username), self.assertRaises(appeal_service.AppealError):
                appeal_service.file_score_appeal(self.revision, user, "arithmetic", REASON)
        self.team.captain.is_active = False
        self.team.captain.save()
        with self.assertRaises(appeal_service.AppealError):
            self.file()
        self.assertFalse(Appeal.objects.exists())

    def test_configured_publication_window_and_procedural_fields_are_enforced(self):
        row = self.comp.settings
        row.results_published = False
        row.save()
        with self.assertRaisesMessage(appeal_service.AppealError, "not been published"):
            self.file()
        row.results_published = True
        row.appeal_window_days = 1
        row.results_published_at = timezone.now() - timezone.timedelta(hours=25)
        row.save()
        with self.assertRaisesMessage(appeal_service.AppealError, "24 hours"):
            self.file()
        row.results_published_at = timezone.now() - timezone.timedelta(hours=23)
        row.save()
        for grounds, reason in (("more points", REASON), ("process", "short"), ("process", "x" * 5001)):
            with self.assertRaises(appeal_service.AppealError):
                appeal_service.file_score_appeal(self.revision, self.team.captain, grounds, reason)
        self.assertIsNotNone(self.file().pk)

    def test_active_appeal_is_unique_across_score_revisions(self):
        self.file()
        changed = self.award("availability", 8).revisions.order_by("-sequence").first()
        with self.assertRaisesMessage(appeal_service.AppealError, "component is already open"):
            self.file(changed)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Appeal.objects.bulk_create([Appeal(score_revision=changed, score_event=self.event,
                                                team=self.team, grounds="process", reason=REASON)])

    def test_database_and_model_require_exactly_one_owned_result(self):
        submission = self.submission()
        for fields in ({}, {"submission": submission, "score_revision": self.revision, "score_event": self.event}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Appeal.objects.bulk_create([Appeal(team=self.team, grounds="process", reason=REASON, **fields)])
        with self.assertRaises(ValidationError):
            Appeal.objects.create(score_revision=self.revision, team=self.rival, grounds="process", reason=REASON)

    def test_filed_subject_and_reason_cannot_be_rewritten(self):
        appeal = self.file()
        appeal.reason = "An unrelated explanation" * 3
        with self.assertRaises(ValidationError):
            appeal.save()

    def test_second_judge_must_be_independent_active_and_outside_the_team(self):
        appeal = self.file()
        with self.assertRaisesMessage(appeal_service.AppealError, "scored this score component"):
            appeal_service.assign_second_judge(appeal, self.first, by=self.staff)
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.assign_second_judge(appeal, self.staff, by=self.staff)
        self.third.is_active = False
        self.third.save()
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.assign_second_judge(appeal, self.third, by=self.staff)
        TeamMember.objects.create(team=self.team, user=self.second, student_name="Reviewer", email="reviewer@example.test")
        with self.assertRaises(appeal_service.AppealError):
            self.assign(appeal)

    def test_assignment_is_staff_only_and_all_decision_revisions_are_excluded(self):
        self.award("availability", 8, judge=self.third)
        appeal = self.file()
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.assign_second_judge(appeal, self.second, by=self.first)
        eligible = appeal_service.eligible_second_judges(appeal)
        self.assertNotIn(self.first, eligible)
        self.assertNotIn(self.third, eligible)
        self.assertIn(self.second, eligible)

    def test_only_assigned_independent_judge_can_resolve_and_score_is_unchanged(self):
        appeal = self.assign(self.file())
        for user in (self.first, self.third, self.staff, self.team.captain):
            with self.assertRaises(appeal_service.AppealError):
                appeal_service.resolve_appeal(appeal, user, "upheld", RESOLUTION)
        for status, explanation in (("withdrawn", RESOLUTION), ("upheld", "short")):
            with self.assertRaises(appeal_service.AppealError):
                appeal_service.resolve_appeal(appeal, self.second, status, explanation)
        resolved = appeal_service.resolve_appeal(appeal, self.second, "upheld", RESOLUTION)
        self.assertEqual(resolved.resolved_by, self.second)
        self.assertIsNotNone(resolved.resolved_at)
        self.event.refresh_from_db()
        self.assertEqual(self.event.marks, 7)
        self.assertEqual(ScoreRevision.objects.count(), 1)
        self.archive.refresh_from_db()
        self.assertFalse(self.archive.appeal_hold)
        self.assertEqual(self.archive.retain_until, self.comp.settings.appeal_window_closes_at)
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.resolve_appeal(resolved, self.second, "rejected", RESOLUTION)

    def test_independence_is_rechecked_if_reviewer_later_changes_the_score(self):
        appeal = self.assign(self.file())
        self.award("availability", 8, judge=self.second)
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.resolve_appeal(appeal, self.second, "upheld", RESOLUTION)

    def test_shared_evidence_stays_held_until_every_linked_appeal_closes(self):
        first = self.assign(self.file())
        other = self.award("detected", 4).revisions.get()
        second = self.assign(self.file(other))
        appeal_service.resolve_appeal(first, self.second, "rejected", RESOLUTION)
        self.archive.refresh_from_db()
        self.assertTrue(self.archive.appeal_hold)
        appeal_service.resolve_appeal(second, self.second, "upheld", RESOLUTION)
        self.archive.refresh_from_db()
        self.assertFalse(self.archive.appeal_hold)

    def test_withdrawal_is_captain_only_and_does_not_shorten_retention(self):
        appeal = self.file()
        later = self.comp.settings.appeal_window_closes_at + timezone.timedelta(days=2)
        self.archive.refresh_from_db()
        self.archive.retain_until = later
        self.archive.save(update_fields=["retain_until"])
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.withdraw_appeal(appeal, self.staff, "The issue has been corrected.")
        withdrawn = appeal_service.withdraw_appeal(appeal, self.team.captain, "The issue has been corrected.")
        self.assertEqual(withdrawn.status, Appeal.Status.WITHDRAWN)
        self.archive.refresh_from_db()
        self.assertFalse(self.archive.appeal_hold)
        self.assertEqual(self.archive.retain_until, later)

    def test_missing_archive_does_not_remove_the_right_to_appeal(self):
        identifier = str(self.archive.pk)
        self.archive.delete()
        appeal = self.file()
        self.assertEqual(appeal.evidence_gaps, [identifier])
        self.assertFalse(appeal.retained_evidence.exists())

    def test_team_page_files_an_appeal_and_displays_decision_and_deadline(self):
        self.client.force_login(self.team.captain)
        url = reverse("scoring:revision", args=[self.revision.pk])
        self.assertContains(self.client.get(url), "File appeal for revision")
        response = self.client.post(reverse("scoring:file_appeal", args=[self.revision.pk]), {"grounds": "arithmetic", "reason": REASON})
        self.assertRedirects(response, url)
        appeal = self.assign(Appeal.objects.get())
        appeal_service.resolve_appeal(appeal, self.second, "upheld", RESOLUTION)
        self.assertContains(self.client.get(url), RESOLUTION)
        self.assertContains(self.client.get(url), "Upheld")
        self.client.force_login(self.rival.captain)
        self.assertEqual(self.client.post(reverse("scoring:file_appeal", args=[self.revision.pk]), {"grounds": "arithmetic", "reason": REASON}).status_code, 404)

    def test_captain_routes_reject_get_and_missing_csrf(self):
        self.client.force_login(self.team.captain)
        route = reverse("scoring:file_appeal", args=[self.revision.pk])
        self.assertEqual(self.client.get(route).status_code, 405)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.team.captain)
        self.assertEqual(strict.post(route, {"grounds": "arithmetic", "reason": REASON}).status_code, 403)
        self.assertFalse(Appeal.objects.exists())

    def test_internal_queue_assigns_and_assigned_reviewer_resolves(self):
        appeal = self.file()
        url = reverse("judging:appeal_detail", args=[appeal.pk])
        self.client.force_login(self.team.captain)
        self.assertEqual(self.client.get(reverse("judging:appeal_list")).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("judging:appeal_list")), self.team.team_name)
        self.assertEqual(self.client.post(url, {"action": "assign", "judge": self.second.pk}).status_code, 302)
        self.assertEqual(self.client.post(url, {"action": "resolve", "status": "upheld", "resolution": RESOLUTION}).status_code, 400)
        self.client.force_login(self.third)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.second)
        self.assertContains(self.client.get(url), "Record the appeal decision")
        self.assertEqual(self.client.post(url, {"action": "resolve", "status": "upheld", "resolution": "short"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"action": "resolve", "status": "upheld", "resolution": RESOLUTION}).status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, Appeal.Status.UPHELD)
        self.assertEqual(self.event.revisions.count(), 1)

    def test_withdrawal_route_preserves_ownership_and_returns_to_result(self):
        appeal = self.file()
        route = reverse("judging:appeal_withdraw", args=[appeal.pk])
        self.client.force_login(self.rival.captain)
        self.assertEqual(self.client.post(route, {"reason": RESOLUTION}).status_code, 404)
        self.client.force_login(self.team.captain)
        self.assertEqual(self.client.get(route).status_code, 405)
        response = self.client.post(route, {"reason": RESOLUTION})
        self.assertRedirects(response, reverse("scoring:revision", args=[self.revision.pk]))
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, Appeal.Status.WITHDRAWN)

    def test_score_appeal_notifications_use_real_authorized_links(self):
        with self.captureOnCommitCallbacks(execute=True):
            appeal = self.file()
        received = Notification.objects.get(user=self.team.captain, type=Notification.Type.APPEAL_SUBMITTED)
        self.assertEqual(received.link, reverse("scoring:revision", args=[self.revision.pk]))
        staff = Notification.objects.get(user=self.staff, type=Notification.Type.APPEAL_SUBMITTED)
        self.assertEqual(staff.link, reverse("judging:appeal_detail", args=[appeal.pk]))
        appeal = self.assign(appeal)
        with self.captureOnCommitCallbacks(execute=True):
            appeal_service.resolve_appeal(appeal, self.second, "upheld", RESOLUTION)
        decision = Notification.objects.get(user=self.team.captain, type=Notification.Type.APPEAL_RESOLVED)
        self.assertIn(RESOLUTION, decision.body)
        self.assertEqual(decision.link, received.link)

    def test_existing_submission_appeal_workflow_and_notifications_are_preserved(self):
        submission = self.submission()
        with self.captureOnCommitCallbacks(execute=True):
            appeal = appeal_service.file_appeal(submission, self.team.captain, "arithmetic", REASON)
        self.assertEqual(appeal.submission_id, submission.pk)
        self.assertIsNone(appeal.score_revision_id)
        with self.assertRaises(appeal_service.AppealError):
            appeal_service.file_appeal(submission, self.team.captain, "arithmetic", REASON)
        with self.assertRaisesMessage(appeal_service.AppealError, "scored this submission"):
            appeal_service.assign_second_judge(appeal, self.first)
        self.assign(appeal)
        with self.captureOnCommitCallbacks(execute=True):
            appeal_service.resolve_appeal(appeal, self.second, "rejected", RESOLUTION)
        received = Notification.objects.get(user=self.team.captain, type=Notification.Type.APPEAL_SUBMITTED)
        decision = Notification.objects.get(user=self.team.captain, type=Notification.Type.APPEAL_RESOLVED)
        self.assertEqual(received.link, reverse("submissions:detail", args=[submission.pk]))
        self.assertEqual(decision.link, received.link)
