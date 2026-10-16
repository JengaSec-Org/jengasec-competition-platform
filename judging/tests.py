"""Judge assignment: who sees which submission, and with what in it."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team, TeamMember, UserProfile
from competitions.models import Competition
from judging.models import CriterionScore, Evaluation
from rubrics.models import Rubric, RubricCriterion
from services import judge_assignment_service as assignments
from services import submission_service as svc, team_service
from submissions.models import Submission, SubmissionType

PASSWORD = "Str0ng-pass-for-tests!"


class AssignmentBase(TestCase):
    def setUp(self):
        self.comp = Competition.objects.create(
            name="JS26 test", start_date=datetime.date(2026, 10, 14),
            end_date=datetime.date(2026, 10, 17), status=Competition.Status.OPEN,
        )
        self.stype = SubmissionType.objects.get(name=svc.APPLICATION_BLUE_PROPOSAL)
        self.rubric = Rubric.objects.create(
            competition=self.comp, submission_type=self.stype, title="Blue rubric",
        )
        self.c_arch = RubricCriterion.objects.create(
            rubric=self.rubric, criterion="Architecture quality",
            weight=Decimal("0.5"), max_score=Decimal("10"), display_order=1,
        )
        self.c_risk = RubricCriterion.objects.create(
            rubric=self.rubric, criterion="Risk management",
            weight=Decimal("0.5"), max_score=Decimal("10"), display_order=2,
        )
        self.staff = User.objects.create_superuser("admin", "admin@strathmore.edu", PASSWORD)
        self.judge_a = self.make_judge("judy")
        self.judge_b = self.make_judge("kamau")
        self.captain, self.submission = self.make_submission("Nyati", "JS26-B-001", "amina")

    def make_judge(self, username):
        user = User.objects.create_user(username, f"{username}@strathmore.edu", PASSWORD)
        user.profile.role = UserProfile.Role.JUDGE
        user.profile.save()
        return user

    def make_submission(self, team_name, identifier, captain_name):
        captain = User.objects.create_user(captain_name, f"{captain_name}@strathmore.edu", PASSWORD)
        captain.profile.email_verified_at = timezone.now()
        captain.profile.save()
        team = Team.objects.create(
            competition=self.comp, team_name=team_name, team_type=Team.TeamType.BLUE,
            track=Team.Track.APPLICATION, captain=captain, team_identifier=identifier,
            status=Team.Status.APPROVED,
        )
        TeamMember.objects.create(team=team, user=captain, student_name=captain_name,
                                  email=captain.email, role=TeamMember.MemberRole.CAPTAIN)
        team_service.assign_platform_role(captain, team)
        submission = svc.get_or_create_thread(team, self.comp, self.stype)
        Submission.objects.filter(pk=submission.pk).update(status=Submission.Status.SUBMITTED)
        submission.refresh_from_db()
        return captain, submission

    def run_model(self, submission):
        """What score_proposals leaves behind: a judge-less evaluation."""
        ai = Evaluation.objects.create(
            submission=submission, rubric=self.rubric, judge=None,
            status=Evaluation.Status.AI_COMPLETE,
        )
        CriterionScore.objects.create(
            evaluation=ai, criterion=self.c_arch, ai_score=Decimal("7"),
            confidence=Decimal("0.9"), reasoning="Clear diagram.", evidence="Section 3",
        )
        CriterionScore.objects.create(
            evaluation=ai, criterion=self.c_risk, ai_score=Decimal("4"),
            confidence=Decimal("0.4"), reasoning="Thin.", evidence="Section 5",
        )
        return ai


class AssignServiceTests(AssignmentBase):
    def test_assign_copies_the_models_suggestions(self):
        self.run_model(self.submission)

        evaluation, created = assignments.assign(self.submission, self.judge_a, by=self.staff)

        self.assertTrue(created)
        self.assertEqual(evaluation.status, Evaluation.Status.AI_COMPLETE)
        scores = {s.criterion_id: s for s in evaluation.criterion_scores.all()}
        self.assertEqual(scores[self.c_arch.pk].ai_score, Decimal("7"))
        self.assertEqual(scores[self.c_arch.pk].final_score, Decimal("7"))
        self.assertEqual(scores[self.c_risk.pk].reasoning, "Thin.")
        self.assertIsNone(scores[self.c_arch.pk].judge_score)

    def test_assign_before_the_model_leaves_blank_scores_then_fills_them(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        self.assertEqual(evaluation.status, Evaluation.Status.PENDING)
        self.assertEqual(evaluation.criterion_scores.count(), 2)
        self.assertFalse(evaluation.criterion_scores.exclude(ai_score=None).exists())

        self.run_model(self.submission)
        assignments.share_ai_scores(self.submission, self.rubric)

        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, Evaluation.Status.AI_COMPLETE)
        self.assertEqual(
            evaluation.criterion_scores.get(criterion=self.c_arch).ai_score, Decimal("7")
        )

    def test_share_never_overwrites_a_judges_score(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        score = evaluation.criterion_scores.get(criterion=self.c_arch)
        score.apply_judge_score(Decimal("9"), judge=self.judge_a, reason="Strong.")

        self.run_model(self.submission)
        assignments.share_ai_scores(self.submission, self.rubric)

        score.refresh_from_db()
        self.assertEqual(score.final_score, Decimal("9"))
        self.assertEqual(score.ai_score, Decimal("7"))

    def test_repeat_assign_changes_nothing(self):
        first, created = assignments.assign(self.submission, self.judge_a)
        second, created_again = assignments.assign(self.submission, self.judge_a)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Evaluation.objects.filter(judge=self.judge_a).count(), 1)

    def test_only_judges_can_be_assigned(self):
        with self.assertRaises(assignments.AssignmentError):
            assignments.assign(self.submission, self.captain)

    def test_no_rubric_is_refused(self):
        self.rubric.is_active = False
        self.rubric.save()
        with self.assertRaises(assignments.AssignmentError):
            assignments.assign(self.submission, self.judge_a)

    def test_unassign_before_scoring(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        assignments.unassign(evaluation)
        self.assertFalse(Evaluation.objects.filter(pk=evaluation.pk).exists())

    def test_unassign_refused_once_the_judge_has_scored(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        evaluation.criterion_scores.get(criterion=self.c_arch).apply_judge_score(
            Decimal("6"), judge=self.judge_a, reason="Checked.",
        )

        with self.assertRaises(assignments.AssignmentError):
            assignments.unassign(evaluation)
        self.assertTrue(Evaluation.objects.filter(pk=evaluation.pk).exists())

    def test_distribute_balances_by_open_load(self):
        _, second = self.make_submission("Simba", "JS26-B-002", "baraka")
        _, third = self.make_submission("Chui", "JS26-B-003", "chebet")
        # judge_a already carries one review elsewhere.
        assignments.assign(third, self.judge_a)

        count, skipped = assignments.distribute(
            Submission.objects.filter(pk__in=[self.submission.pk, second.pk, third.pk]),
            per_submission=1,
        )

        self.assertEqual(count, 2)
        self.assertEqual(skipped, [])
        loads = {
            j.username: j.open_reviews for j in assignments.judges()
        }
        self.assertEqual(loads, {"judy": 2, "kamau": 1})
        for submission in (self.submission, second, third):
            self.assertEqual(Evaluation.objects.filter(submission=submission).count(), 1)

    def test_distribute_two_per_submission_never_doubles_a_judge(self):
        count, _ = assignments.distribute(
            Submission.objects.filter(pk=self.submission.pk), per_submission=3,
        )
        self.assertEqual(count, 2)  # only two judges exist
        judges = list(
            Evaluation.objects.filter(submission=self.submission).values_list("judge_id", flat=True)
        )
        self.assertEqual(sorted(judges), sorted([self.judge_a.pk, self.judge_b.pk]))


class AssignmentAccessTests(AssignmentBase):
    def test_other_judge_gets_404_on_someone_elses_review(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        self.client.force_login(self.judge_b)

        url = reverse("judging:ai_result_detail", args=[evaluation.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.post(url, {"review_action": "approve"})
        evaluation.refresh_from_db()
        self.assertNotEqual(evaluation.status, Evaluation.Status.APPROVED)

    def test_competitor_gets_404_on_a_review(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        self.client.force_login(self.captain)

        url = reverse("judging:ai_result_detail", args=[evaluation.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_assigned_judge_sees_it_in_their_queue(self):
        self.run_model(self.submission)
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        self.client.force_login(self.judge_a)

        queue = self.client.get(reverse("judging:ai_results"))
        self.assertContains(queue, "Nyati")
        detail = self.client.get(reverse("judging:ai_result_detail", args=[evaluation.pk]))
        self.assertEqual(detail.status_code, 200)

    def test_first_score_on_a_blank_criterion_needs_no_reason(self):
        evaluation, _ = assignments.assign(self.submission, self.judge_a)
        self.client.force_login(self.judge_a)

        self.client.post(
            reverse("judging:ai_result_detail", args=[evaluation.pk]),
            {"review_action": "save_overrides", f"score_{self.c_arch.pk}": "8"},
        )
        score = evaluation.criterion_scores.get(criterion=self.c_arch)
        self.assertEqual(score.final_score, Decimal("8"))

    def test_competitor_and_judge_cannot_open_assign_page(self):
        url = reverse("judging:assign_judges")
        for user in (self.captain, self.judge_a):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, user.username)
            self.assertTrue(response["Location"].startswith("/login/"), user.username)

    def test_organiser_assigns_from_the_page(self):
        self.client.force_login(self.staff)
        url = reverse("judging:assign_judges")

        page = self.client.get(url)
        self.assertContains(page, "Nyati")
        self.assertContains(page, "judy")

        self.client.post(url, {
            "action": "assign", "submission_id": self.submission.pk, "judge_id": self.judge_a.pk,
        })
        evaluation = Evaluation.objects.get(submission=self.submission, judge=self.judge_a)

        self.client.post(url, {"action": "unassign", "evaluation_id": evaluation.pk})
        self.assertFalse(Evaluation.objects.filter(pk=evaluation.pk).exists())

        self.client.post(url, {"action": "distribute", "per_submission": "1"})
        self.assertEqual(Evaluation.objects.filter(submission=self.submission).count(), 1)
