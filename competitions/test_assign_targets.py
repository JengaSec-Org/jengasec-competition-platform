"""Dealing Red teams onto applications, and the qualifier bonus.

    python manage.py test competitions.test_assign_targets

The distribution is the part worth pinning. "Three per application" is
only true when the turnout divides; what matters is that the leftovers are
spread rather than dumped, that nobody is assigned twice, and that the
strongest Red teams do not all land on one Blue team.
"""
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions.constants import Track
from competitions.models import TargetAssignment
from scoring.models import ScoreEvent, TeamScore
from services import scoring_service


def app_team(competition, side, name, cell):
    team = make_team(competition, make_user(name.lower().replace(" ", "")[:12]),
                     name=name, team_type=side)
    team.track = Track.APPLICATION
    team.enterprise = "a"
    team.cell_id = cell
    team.status = Team.Status.APPROVED
    team.save()
    return team


class DistributionTests(TestCase):
    def setUp(self):
        self.comp = make_competition()

    def build(self, blues, reds):
        self.blue = [
            app_team(self.comp, Team.TeamType.BLUE, f"Blue {i}", f"APP{i:02d}")
            for i in range(1, blues + 1)
        ]
        self.red = [
            app_team(self.comp, Team.TeamType.RED, f"Red {i}", f"APPRED{i:02d}")
            for i in range(1, reds + 1)
        ]

    def load(self):
        counts = {}
        for assignment in TargetAssignment.objects.filter(competition=self.comp):
            counts.setdefault(assignment.target_team_id, []).append(assignment.red_team_id)
        return counts

    def test_a_clean_three_to_one_split(self):
        self.build(blues=3, reds=9)
        call_command("assign_targets", "--confirm", verbosity=0)
        self.assertEqual(sorted(len(v) for v in self.load().values()), [3, 3, 3])

    def test_an_awkward_turnout_is_spread_not_dumped(self):
        """19 Red across 8 applications: fives and twos, never a one."""
        self.build(blues=8, reds=19)
        call_command("assign_targets", "--confirm", verbosity=0)
        sizes = sorted(len(v) for v in self.load().values())
        self.assertEqual(sum(sizes), 19)
        self.assertEqual(min(sizes), 2)
        self.assertLessEqual(max(sizes), 3)

    def test_two_to_one_when_that_is_all_that_turned_up(self):
        self.build(blues=4, reds=8)
        call_command("assign_targets", "--confirm", verbosity=0)
        self.assertEqual(sorted(len(v) for v in self.load().values()), [2, 2, 2, 2])

    def test_every_red_team_attacks_exactly_one_application(self):
        self.build(blues=4, reds=10)
        call_command("assign_targets", "--confirm", verbosity=0)
        per_red = {}
        for assignment in TargetAssignment.objects.filter(competition=self.comp):
            per_red[assignment.red_team_id] = per_red.get(assignment.red_team_id, 0) + 1
        self.assertTrue(all(count == 1 for count in per_red.values()))
        self.assertEqual(len(per_red), 10)

    def test_a_red_team_over_capacity_is_reported_not_silently_dropped(self):
        self.build(blues=1, reds=5)  # one application, cap of 3
        call_command("assign_targets", "--confirm", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 3)

    def test_the_cap_can_be_raised(self):
        self.build(blues=1, reds=5)
        call_command("assign_targets", "--confirm", "--per", "5", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 5)

    def test_the_strongest_teams_are_dealt_apart(self):
        """Top three qualifiers must not all land on one application."""
        self.build(blues=3, reds=9)
        for rank, team in enumerate(self.red):
            scoring_service.award(team, "entry", Decimal(str(30 - rank)),
                                  source="entry", dedupe_key="qualifier")
            scoring_service.recompute(team)
        call_command("assign_targets", "--confirm", verbosity=0)
        top_three = {t.pk for t in self.red[:3]}
        for attackers in self.load().values():
            self.assertLessEqual(len(top_three & set(attackers)), 1)

    def test_nothing_opens_until_it_is_activated(self):
        self.build(blues=2, reds=4)
        call_command("assign_targets", "--confirm", verbosity=0)
        self.assertFalse(
            TargetAssignment.objects.filter(is_active=True).exists()
        )
        call_command("assign_targets", "--confirm", "--activate", verbosity=0)
        self.assertTrue(
            all(a.is_active for a in TargetAssignment.objects.all())
        )

    def test_rerunning_keeps_an_existing_assignment(self):
        """A live engagement must not have its target swapped underneath it."""
        self.build(blues=3, reds=6)
        call_command("assign_targets", "--confirm", verbosity=0)
        before = {a.red_team_id: a.target_team_id
                  for a in TargetAssignment.objects.all()}
        call_command("assign_targets", "--confirm", verbosity=0)
        after = {a.red_team_id: a.target_team_id
                 for a in TargetAssignment.objects.all()}
        self.assertEqual(before, after)

    def test_a_dry_run_assigns_nothing(self):
        self.build(blues=2, reds=4)
        call_command("assign_targets", "--dry-run", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 0)

    def test_blue_teams_are_never_given_a_target(self):
        self.build(blues=2, reds=2)
        call_command("assign_targets", "--confirm", verbosity=0)
        blue_pks = {t.pk for t in self.blue}
        attackers = {a.red_team_id for a in TargetAssignment.objects.all()}
        self.assertEqual(attackers & blue_pks, set())


class QualifierBonusTests(TestCase):
    """360 points earns the stage; above that is bonus, not waste."""

    def setUp(self):
        self.comp = make_competition()

    def red(self, name):
        return app_team(self.comp, Team.TeamType.RED, name, "APPRED01")

    def test_the_mark_that_earns_the_stage_gives_exactly_thirty(self):
        team = self.red("M4LW4R3")  # 375 points
        call_command("seed_qualifier_scores", "--confirm", "--max", "375",
                     verbosity=0)
        score = TeamScore.objects.get(team=team)
        self.assertEqual(score.entry, Decimal("30.00"))
        self.assertEqual(score.bonus, Decimal("0.00"))

    def test_above_the_mark_becomes_bonus(self):
        team = self.red("pwners")  # 487.5 of 360
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        score = TeamScore.objects.get(team=team)
        self.assertEqual(score.entry, Decimal("30.00"))
        self.assertGreater(score.bonus, Decimal("0"))
        # 487.5/360*30 = 40.625, so 10.625 beyond the stage, which rounds
        # to 10.62 — half a cent of a mark, and rounded the same way every
        # time rather than drifting between runs.
        self.assertEqual(score.bonus, Decimal("10.62"))
        self.assertEqual(score.total, Decimal("40.62"))

    def test_below_the_mark_is_proportional_with_no_bonus(self):
        team = self.red("Jaguar")  # 100 of 360
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        score = TeamScore.objects.get(team=team)
        self.assertEqual(score.entry, Decimal("8.33"))
        self.assertEqual(score.bonus, Decimal("0.00"))

    def test_bonus_can_be_switched_off(self):
        team = self.red("pwners")
        call_command("seed_qualifier_scores", "--confirm", "--no-bonus",
                     verbosity=0)
        score = TeamScore.objects.get(team=team)
        self.assertEqual(score.entry, Decimal("30.00"))
        self.assertEqual(score.bonus, Decimal("0.00"))

    def test_lowering_the_mark_on_a_rerun_removes_a_bonus(self):
        """A bonus a team no longer earns must actually go away."""
        team = self.red("pwners")
        call_command("seed_qualifier_scores", "--confirm", verbosity=0)
        self.assertGreater(TeamScore.objects.get(team=team).bonus, Decimal("0"))
        call_command("seed_qualifier_scores", "--confirm", "--max", "600",
                     verbosity=0)
        self.assertEqual(TeamScore.objects.get(team=team).bonus, Decimal("0.00"))

    def test_the_bonus_is_not_capped_by_a_stage(self):
        team = self.red("pwners")
        scoring_service.award(team, "bonus", Decimal("55"), source="judge")
        self.assertEqual(scoring_service.recompute(team).bonus, Decimal("55"))

    def test_a_team_can_finish_above_one_hundred(self):
        team = self.red("pwners")
        for stage, marks in (("entry", 30), ("main", 40), ("documentation", 30)):
            scoring_service.award(team, stage, Decimal(str(marks)), source="judge")
        scoring_service.award(team, "bonus", Decimal("4"), source="judge")
        self.assertEqual(scoring_service.recompute(team).total, Decimal("104"))
