"""The proposal rubric, and carrying the qualifier into the Entry stage.

    python manage.py test competitions.test_proposal_rubric

Two things a team would appeal over: a proposal mark that does not follow
the published weights, and a qualifier score landing on the wrong team.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions import proposal_rubric
from competitions.constants import Track
from competitions.management.commands.seed_qualifier_scores import (
    QUALIFIER_2026, normalise_name,
)
from scoring.models import ScoreEvent, TeamScore
from services import scoring_service


class RubricDataTests(TestCase):
    def test_the_blue_rubric_is_the_guide_s_twelve_sections(self):
        titles = [title for _, title, _, _ in proposal_rubric.BLUE_SECTIONS]
        self.assertEqual(len(titles), 12)
        self.assertEqual(titles[0], "Executive Summary")
        self.assertEqual(titles[7], "Threat Model")
        self.assertEqual(titles[-1], "Development Timeline")

    def test_the_published_weights_are_kept_as_published(self):
        """They sum to 150 in the Guide; we do not quietly rescale them."""
        self.assertEqual(proposal_rubric.total_weight("blue"), 150)
        weights = {t: w for _, t, w, _ in proposal_rubric.BLUE_SECTIONS}
        self.assertEqual(weights["Threat Model"], 20)
        self.assertEqual(weights["Security Objectives"], 20)
        self.assertEqual(weights["Problem Statement"], 10)

    def test_framing_sections_carry_no_mark(self):
        weights = {t: w for _, t, w, _ in proposal_rubric.BLUE_SECTIONS}
        self.assertEqual(weights["Executive Summary"], 0)
        self.assertEqual(weights["Objectives"], 0)

    def test_the_red_rubric_is_the_guide_s_fifteen_sections(self):
        self.assertEqual(len(proposal_rubric.RED_SECTIONS), 15)

    def test_a_perfect_proposal_normalises_to_one_hundred(self):
        perfect = {t: 100 for _, t, _, _ in proposal_rubric.BLUE_SECTIONS}
        self.assertEqual(proposal_rubric.normalise(perfect), 100.0)

    def test_an_empty_proposal_normalises_to_zero(self):
        self.assertEqual(proposal_rubric.normalise({}), 0.0)

    def test_a_missing_section_is_a_zero_not_an_average(self):
        """The Guide: 'not averaged away or inferred from elsewhere'."""
        scored = {t: 100 for _, t, _, _ in proposal_rubric.BLUE_SECTIONS}
        del scored["Threat Model"]          # worth 20 of 150
        self.assertAlmostEqual(proposal_rubric.normalise(scored), 86.67, places=1)

    def test_weighting_follows_the_published_ratios(self):
        """Threat Model is worth twice Problem Statement.

        Compared to one decimal place: `normalise` rounds its result, so
        doubling a rounded 6.67 is not bit-identical to a rounded 13.33.
        """
        only_threat = proposal_rubric.normalise({"Threat Model": 100})
        only_problem = proposal_rubric.normalise({"Problem Statement": 100})
        self.assertAlmostEqual(only_threat, only_problem * 2, places=1)

    def test_a_proposal_mark_becomes_the_entry_stage_share(self):
        self.assertEqual(proposal_rubric.to_entry_marks(100), 30.0)
        self.assertEqual(proposal_rubric.to_entry_marks(50), 15.0)


class SeedRubricTests(TestCase):
    def setUp(self):
        self.comp = make_competition()

    def test_it_creates_the_published_criteria(self):
        from rubrics.models import Rubric

        call_command("seed_proposal_rubrics", "--confirm", verbosity=0)
        blue = Rubric.objects.filter(
            submission_type__name="Application Blue Proposal"
        ).first()
        self.assertIsNotNone(blue)
        self.assertEqual(blue.criteria.count(), 12)
        self.assertEqual(blue.total_weight(), Decimal("150.00"))
        self.assertTrue(blue.weights_valid())

    def test_running_it_twice_does_not_duplicate_criteria(self):
        from rubrics.models import Rubric

        call_command("seed_proposal_rubrics", "--confirm", verbosity=0)
        call_command("seed_proposal_rubrics", "--confirm", verbosity=0)
        blue = Rubric.objects.get(submission_type__name="Application Blue Proposal")
        self.assertEqual(blue.criteria.count(), 12)

    def test_a_dry_run_writes_nothing(self):
        from rubrics.models import RubricCriterion

        before = RubricCriterion.objects.count()
        call_command("seed_proposal_rubrics", "--dry-run", verbosity=0)
        self.assertEqual(RubricCriterion.objects.count(), before)


class QualifierSeedTests(TestCase):
    def setUp(self):
        self.comp = make_competition()

    def red(self, name):
        team = make_team(self.comp, make_user(name[:8].lower().replace(" ", "")),
                         name=name, team_type=Team.TeamType.RED)
        team.track = Track.APPLICATION
        team.status = Team.Status.APPROVED
        team.save()
        return team

    def test_the_transcribed_scoreboard_is_intact(self):
        self.assertEqual(len(QUALIFIER_2026), 30)
        self.assertEqual(QUALIFIER_2026[0], ("pwners", 487.5))
        self.assertEqual(sum(1 for _, p in QUALIFIER_2026 if p > 0), 22)

    def test_names_match_despite_spacing_and_punctuation(self):
        self.assertEqual(normalise_name("FAST & FURIOUS"), normalise_name("fast and furious".replace(" and ", "")))
        self.assertEqual(normalise_name("Chmod 777"), normalise_name("chmod777"))
        self.assertEqual(normalise_name("0x1_Z3r0_D4y"), "0x1z3r0d4y")

    def test_a_team_past_the_mark_gets_the_full_entry_stage(self):
        """487.5 is past the 360 that earns the stage, so Entry caps at 30."""
        team = self.red("pwners")
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertEqual(TeamScore.objects.get(team=team).entry, Decimal("30.00"))

    def test_marks_scale_proportionally_against_the_mark(self):
        """Not against whoever happened to come first: 360 points is the ruler."""
        top, mid = self.red("pwners"), self.red("Jaguar")  # 487.5 and 100
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertEqual(TeamScore.objects.get(team=top).entry, Decimal("30.00"))
        self.assertEqual(
            float(TeamScore.objects.get(team=mid).entry), round(100 / 360 * 30, 2)
        )

    def test_a_fixed_ceiling_can_be_given_instead(self):
        team = self.red("Jaguar")  # 100 points
        call_command("seed_qualifier_scores", "--confirm", "--max", "200", verbosity=0)
        self.assertEqual(TeamScore.objects.get(team=team).entry, Decimal("15.00"))

    def test_a_zero_score_still_records_a_zero(self):
        team = self.red("Null")
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertEqual(TeamScore.objects.get(team=team).entry, Decimal("0.00"))

    def test_running_it_twice_does_not_double_the_mark(self):
        team = self.red("pwners")
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertEqual(
            ScoreEvent.objects.filter(team=team, dedupe_key="qualifier").count(), 1
        )
        self.assertEqual(TeamScore.objects.get(team=team).entry, Decimal("30.00"))

    def test_a_dry_run_awards_nothing(self):
        self.red("pwners")
        call_command("seed_qualifier_scores", "--dry-run", verbosity=0)
        self.assertEqual(ScoreEvent.objects.count(), 0)

    def test_a_team_not_on_the_scoreboard_is_not_scored(self):
        team = self.red("Nobody Here")
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertFalse(ScoreEvent.objects.filter(team=team).exists())

    def test_a_blue_team_is_never_given_a_qualifier_mark(self):
        blue = make_team(self.comp, make_user("bluecap"), name="pwners",
                         team_type=Team.TeamType.BLUE)
        blue.track = Track.APPLICATION
        blue.save()
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertFalse(ScoreEvent.objects.filter(team=blue).exists())


class SchemePageTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.staff = User.objects.create_user("judge1", password="x", is_staff=True,
                                              is_superuser=True)
        self.team = make_team(self.comp, make_user("cap"), name="Simba",
                              team_type=Team.TeamType.BLUE)
        self.team.track = Track.APPLICATION
        self.team.save()

    def test_a_judge_sees_every_side_and_the_proposal_rubric(self):
        call_command("seed_proposal_rubrics", "--confirm", verbosity=0)
        self.client.force_login(self.staff)
        body = self.client.get(reverse("scoring:scheme")).content.decode()
        for probe in ("Valid and reproducible", "Availability under attack",
                      "Detection precision", "Harmful actions induced",
                      "Threat Model", "Ethics Declaration", "Over the page limit"):
            self.assertIn(probe, body, probe)

    def test_it_says_when_no_rubric_has_been_created(self):
        self.client.force_login(self.staff)
        self.assertContains(
            self.client.get(reverse("scoring:scheme")), "seed_proposal_rubrics"
        )

    def test_a_competitor_cannot_open_it(self):
        self.client.force_login(self.team.captain)
        self.assertNotEqual(
            self.client.get(reverse("scoring:scheme")).status_code, 200
        )


class ProposalMarkTests(TestCase):
    """Carrying an approved evaluation into the Entry stage.

    The arithmetic worth pinning: the rubric's weights total 150, so a
    weighted total is on a 150 scale, not a 100 one. Getting that wrong
    would quietly mark every proposal at two thirds of what it earned.
    """

    def setUp(self):
        from rubrics.models import Rubric

        self.comp = make_competition()
        call_command("seed_proposal_rubrics", "--confirm", verbosity=0)
        self.rubric = Rubric.objects.get(
            submission_type__name="Application Blue Proposal"
        )
        self.team = make_team(self.comp, make_user("blu"), name="Simba",
                              team_type=Team.TeamType.BLUE)
        self.team.track = Track.APPLICATION
        self.team.save()

    def evaluation(self, weighted_total):
        from judging.models import Evaluation
        from submissions.models import Submission, SubmissionType

        submission = Submission.objects.create(
            team=self.team, competition=self.comp,
            submission_type=SubmissionType.objects.get(name="Application Blue Proposal"),
        )
        evaluation = Evaluation.objects.create(
            submission=submission, rubric=self.rubric, judge=None,
            weighted_total=Decimal(str(weighted_total)),
        )
        return evaluation

    def test_a_perfect_proposal_earns_the_whole_entry_stage(self):
        """150 of 150 is 100%, which is all 30 Entry marks."""
        scoring_service.award_for_proposal(self.evaluation(150))
        self.assertEqual(TeamScore.objects.get(team=self.team).entry, Decimal("30.00"))

    def test_half_marks_earn_half_the_stage(self):
        scoring_service.award_for_proposal(self.evaluation(75))
        self.assertEqual(TeamScore.objects.get(team=self.team).entry, Decimal("15.00"))

    def test_a_zero_proposal_earns_nothing(self):
        scoring_service.award_for_proposal(self.evaluation(0))
        self.assertEqual(TeamScore.objects.get(team=self.team).entry, Decimal("0.00"))

    def test_the_mark_never_exceeds_the_stage(self):
        scoring_service.award_for_proposal(self.evaluation(150))
        self.assertLessEqual(TeamScore.objects.get(team=self.team).entry, Decimal("30"))

    def test_the_team_is_told_what_it_scored(self):
        scoring_service.award_for_proposal(self.evaluation(120))
        event = ScoreEvent.objects.get(team=self.team, dedupe_key="proposal")
        self.assertIn("/100", event.note)

    def test_re_approving_replaces_rather_than_adds(self):
        """A revised evaluation corrects the mark; it does not add a second."""
        evaluation = self.evaluation(120)
        scoring_service.award_for_proposal(evaluation)
        evaluation.weighted_total = Decimal("90")
        evaluation.save(update_fields=["weighted_total"])
        scoring_service.award_for_proposal(evaluation)
        self.assertEqual(
            ScoreEvent.objects.filter(team=self.team, dedupe_key="proposal").count(), 1
        )
        self.assertEqual(TeamScore.objects.get(team=self.team).entry, Decimal("18.00"))
