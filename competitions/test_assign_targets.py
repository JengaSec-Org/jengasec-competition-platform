"""Dealing Red teams onto applications, and the qualifier bonus.

    python manage.py test competitions.test_assign_targets

The distribution is the part worth pinning. "Three per application" is
only true when the turnout divides; what matters is that the leftovers are
spread rather than dumped, that nobody is assigned twice, that the draw is
random yet repeatable with a seed, and that no Red team is left without a
target when there are only a few applications.
"""
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
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

    def test_with_no_cap_every_red_team_gets_a_target(self):
        """Few applications must not leave Red teams out (the old cap of 3 did)."""
        self.build(blues=3, reds=14)
        call_command("assign_targets", "--confirm", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 14)
        self.assertEqual(sorted(len(v) for v in self.load().values()), [4, 5, 5])

    def test_an_optional_cap_is_reported_not_silently_dropped(self):
        self.build(blues=1, reds=5)
        call_command("assign_targets", "--confirm", "--per", "3", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 3)

    def test_the_draw_is_random_but_repeatable_with_a_seed(self):
        self.build(blues=3, reds=9)
        draws = []
        for _ in range(2):
            call_command("assign_targets", "--confirm", "--reassign", "--seed", "7", verbosity=0)
            draws.append({a.red_team_id: a.target_team_id for a in TargetAssignment.objects.all()})
        self.assertEqual(draws[0], draws[1])
        call_command("assign_targets", "--confirm", "--reassign", "--seed", "8", verbosity=0)
        other = {a.red_team_id: a.target_team_id for a in TargetAssignment.objects.all()}
        self.assertNotEqual(draws[0], other)

    def test_only_the_named_applications_are_attacked_and_get_endpoints(self):
        self.build(blues=4, reds=6)
        call_command(
            "assign_targets", "--confirm",
            "--app", f"{self.blue[0].team_name}=http://10.250.0.1:30002/",
            "--app", f"{self.blue[2].team_name}=http://10.250.0.1:30010/",
            verbosity=0,
        )
        targets = set(TargetAssignment.objects.values_list("target_team_id", flat=True))
        self.assertEqual(targets, {self.blue[0].pk, self.blue[2].pk})
        self.assertEqual(
            set(TargetAssignment.objects.filter(target_team=self.blue[0])
                .values_list("endpoint", flat=True)),
            {"http://10.250.0.1:30002/"},
        )

    def test_an_unknown_application_name_is_refused(self):
        self.build(blues=2, reds=2)
        with self.assertRaises(CommandError):
            call_command("assign_targets", "--confirm", "--app", "Nobody=http://x/", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 0)

    def test_no_enterprise_or_cell_is_needed(self):
        self.build(blues=3, reds=6)
        Team.objects.update(enterprise="", cell_id="")
        call_command("assign_targets", "--confirm", verbosity=0)
        self.assertEqual(TargetAssignment.objects.count(), 6)

    def decoy_run(self, *extra):
        call_command(
            "assign_targets", "--confirm",
            "--app", f"{self.blue[0].team_name}=http://10.250.0.1:30002/",
            "--app", f"{self.blue[1].team_name}=http://10.250.0.1:30009/",
            "--decoy", "http://10.250.0.1:30006/",
            "--decoy", "http://10.250.0.1:30007/",
            *extra, verbosity=0,
        )
        return {a.red_team_id: a.endpoint.split("  ·  ") for a in TargetAssignment.objects.all()}

    def test_each_red_team_gets_its_target_and_one_decoy(self):
        self.build(blues=2, reds=8)
        shown = self.decoy_run()
        decoys = {"http://10.250.0.1:30006/", "http://10.250.0.1:30007/"}
        counts = {d: 0 for d in decoys}
        for a in TargetAssignment.objects.all():
            urls = shown[a.red_team_id]
            self.assertEqual(len(urls), 2)
            real = {self.blue[0].pk: "http://10.250.0.1:30002/",
                    self.blue[1].pk: "http://10.250.0.1:30009/"}[a.target_team_id]
            self.assertIn(real, urls)
            decoy = (set(urls) - {real}).pop()
            self.assertIn(decoy, decoys)
            counts[decoy] += 1
        self.assertEqual(sorted(counts.values()), [4, 4])  # spread evenly

    def test_the_real_url_is_not_always_first(self):
        self.build(blues=2, reds=12)
        shown = self.decoy_run("--seed", "3")
        reals = {"http://10.250.0.1:30002/", "http://10.250.0.1:30009/"}
        firsts = {urls[0] in reals for urls in shown.values()}
        self.assertEqual(firsts, {True, False})

    def test_a_rerun_keeps_each_teams_decoy(self):
        self.build(blues=2, reds=6)
        before = {k: set(v) for k, v in self.decoy_run().items()}
        after = {k: set(v) for k, v in self.decoy_run().items()}
        self.assertEqual(before, after)

    def test_decoys_are_optional(self):
        self.build(blues=2, reds=4)
        call_command("assign_targets", "--confirm",
                     "--app", f"{self.blue[0].team_name}=http://10.250.0.1:30002/",
                     "--app", f"{self.blue[1].team_name}=http://10.250.0.1:30009/",
                     verbosity=0)
        self.assertTrue(all("·" not in a.endpoint for a in TargetAssignment.objects.all()))

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
