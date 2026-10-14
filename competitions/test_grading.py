"""The marking scheme must add up, and say the same thing everywhere.

    python manage.py test competitions.test_grading

A scheme that sums to 97 is not a rounding problem, it is a team asking
why it was marked out of 97 in front of a room of executives. These tests
exist so that question cannot arise from a typo.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions import grading
from competitions.constants import Track
from services import competition_service


class SchemeTests(TestCase):
    def test_the_three_stages_sum_to_one_hundred(self):
        self.assertEqual(sum(w for _, w in grading.SCORE_WEIGHTS), 100)

    def test_the_published_split(self):
        """Entry 30, main competition 40, documentation 30 (13 Oct 2026)."""
        self.assertEqual(
            grading.SCORE_WEIGHTS,
            [("Entry", 30), ("Main competition", 40), ("Documentation", 30)],
        )

    def test_every_side_adds_up(self):
        self.assertEqual(grading.check(), [])

    def test_each_side_has_its_own_criteria(self):
        """Four sides, four different tables — not one generic list."""
        tables = {
            key: tuple(label for _, label, _, _ in rows)
            for key, rows in grading.MAIN_BY_SIDE.items()
        }
        self.assertEqual(len(tables), 4)
        self.assertEqual(len(set(tables.values())), 4)

    def test_competition_service_re_exports_one_scheme(self):
        """Two copies of the weights is how they drift apart."""
        self.assertIs(competition_service.SCORE_WEIGHTS, grading.SCORE_WEIGHTS)


class SideTests(TestCase):
    def setUp(self):
        self.comp = make_competition()

    def team(self, side, track):
        team = make_team(self.comp, make_user(f"{side}{track}"), name=f"{side}-{track}",
                         team_type=side)
        team.track = track
        team.save()
        return team

    def test_the_side_each_team_is_marked_as(self):
        cases = [
            (Team.TeamType.BLUE, Track.APPLICATION, "application_blue"),
            (Team.TeamType.RED, Track.APPLICATION, "application_red"),
            (Team.TeamType.BLUE, Track.AI, "ai_blue"),
            (Team.TeamType.RED, Track.AI, "ai_red"),
        ]
        for side, track, expected in cases:
            self.assertEqual(grading.side_key(self.team(side, track)), expected)

    def test_a_team_with_no_track_yet_is_not_guessed_at(self):
        team = make_team(self.comp, make_user("notrack"), name="No track")
        team.track = ""
        team.save()
        self.assertIsNone(grading.side_key(team))
        main = next(s for s in grading.stages(team) if s["key"] == "main")
        self.assertEqual(main["rows"], [])

    def test_red_and_blue_are_shown_different_main_criteria(self):
        red = grading.stages(self.team(Team.TeamType.RED, Track.APPLICATION))
        blue = grading.stages(self.team(Team.TeamType.BLUE, Track.APPLICATION))
        red_rows = {r["label"] for s in red if s["key"] == "main" for r in s["rows"]}
        blue_rows = {r["label"] for s in blue if s["key"] == "main" for r in s["rows"]}
        self.assertIn("Valid and reproducible", red_rows)
        self.assertIn("Availability under attack", blue_rows)
        self.assertEqual(red_rows & blue_rows, set())


class ScorecardTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.team = make_team(
            self.comp, make_user("cap"), name="Simba", team_type=Team.TeamType.BLUE
        )
        self.team.track = Track.APPLICATION
        self.team.save()

    def test_the_scorecard_is_out_of_one_hundred(self):
        self.assertEqual(competition_service.scorecard(self.team)["total_max"], 100)

    def test_the_scorecard_follows_the_published_stages(self):
        card = competition_service.scorecard(self.team)
        self.assertEqual(
            [section["name"] for section in card["sections"]],
            [name for name, _ in grading.SCORE_WEIGHTS],
        )

    def test_each_section_is_worth_its_stage_weight(self):
        card = competition_service.scorecard(self.team)
        for section, (_, weight) in zip(card["sections"], grading.SCORE_WEIGHTS):
            self.assertEqual(sum(r["max"] for r in section["rows"]), weight, section["name"])

    def test_entry_is_one_carried_mark_not_a_list_of_criteria(self):
        """It was earned before the event; it is not re-marked here."""
        entry = competition_service.scorecard(self.team)["sections"][0]
        self.assertEqual(len(entry["rows"]), 1)
        self.assertEqual(entry["rows"][0]["max"], 30)


class InfoPageTests(TestCase):
    """Both sides must be able to read how they are marked."""

    def setUp(self):
        self.comp = make_competition()

    def page_for(self, side, track):
        team = make_team(self.comp, make_user(f"{side}{track}"), name=f"{side}-{track}",
                         team_type=side)
        team.track = track
        team.save()
        self.client.force_login(team.captain)
        return self.client.get(reverse("dashboard:info")).content.decode()

    def test_blue_sees_the_split_and_its_own_criteria(self):
        body = self.page_for(Team.TeamType.BLUE, Track.APPLICATION)
        self.assertIn("30%", body)
        self.assertIn("40%", body)
        self.assertIn("Availability under attack", body)
        self.assertNotIn("Valid and reproducible", body)

    def test_red_sees_the_split_and_its_own_criteria(self):
        body = self.page_for(Team.TeamType.RED, Track.APPLICATION)
        self.assertIn("40%", body)
        self.assertIn("Valid and reproducible", body)
        self.assertNotIn("Availability under attack", body)

    def test_both_sides_are_told_the_entry_routes(self):
        for side in (Team.TeamType.BLUE, Team.TeamType.RED):
            body = self.page_for(side, Track.APPLICATION)
            self.assertIn("Hack The Box qualifier", body)
