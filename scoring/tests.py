"""Marks, totals and the standing.

    python manage.py test scoring

The rules worth pinning are the ones a team would appeal over:

* a mark is never counted twice, however often a computation re-runs;
* a judge taking marks away actually takes them away;
* no stage can exceed its published weight;
* the participant board really is delayed, not a cached copy;
* a team can read every mark against itself, and no mark against anyone else.
"""
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Team
from accounts.tests import make_competition, make_team, make_user
from competitions.constants import Track
from competitions.models import TargetAssignment
from cyberrange.models import Finding
from services import competition_service, scoring_service
from scoring.models import ScoreEvent, TeamScore


def red_team(competition, name="Nyati"):
    team = make_team(competition, make_user(name.lower()), name=name,
                     team_type=Team.TeamType.RED)
    team.track = Track.APPLICATION
    team.status = Team.Status.APPROVED
    team.save()
    return team


def blue_team(competition, name="Simba", cell="APP01"):
    team = make_team(competition, make_user(name.lower()), name=name,
                     team_type=Team.TeamType.BLUE)
    team.track = Track.APPLICATION
    team.enterprise = "a"
    team.cell_id = cell
    team.status = Team.Status.APPROVED
    team.save()
    return team


class AwardTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp)

    def test_a_mark_is_recorded_and_totalled(self):
        scoring_service.award(self.team, "documentation", 8, source="judge",
                              criterion="complete")
        score = scoring_service.recompute(self.team)
        self.assertEqual(score.documentation, Decimal("8"))
        self.assertEqual(score.total, Decimal("8"))

    def test_a_keyed_mark_replaces_itself_rather_than_adding(self):
        for value in (5, 7, 6):
            scoring_service.award(self.team, "documentation", value, source="judge",
                                  criterion="complete", dedupe_key="judge:documentation:complete")
        self.assertEqual(ScoreEvent.objects.count(), 1)
        self.assertEqual(scoring_service.recompute(self.team).documentation, Decimal("6"))

    def test_an_unkeyed_mark_is_a_separate_row(self):
        for _ in range(3):
            scoring_service.award(self.team, "documentation", 2, source="judge")
        self.assertEqual(ScoreEvent.objects.count(), 3)
        self.assertEqual(scoring_service.recompute(self.team).documentation, Decimal("6"))

    def test_a_stage_cannot_exceed_its_published_weight(self):
        """Documentation is worth 30, whatever is recorded against it."""
        scoring_service.award(self.team, "documentation", 999, source="judge")
        self.assertEqual(scoring_service.recompute(self.team).documentation, Decimal("30"))

    def test_a_penalty_cannot_push_a_stage_below_zero(self):
        scoring_service.award(self.team, "main", 5, source="judge")
        scoring_service.award(self.team, "main", -50, source="judge", note="Disqualified round")
        self.assertEqual(scoring_service.recompute(self.team).main, Decimal("0"))

    def test_the_total_is_the_three_stages(self):
        scoring_service.award(self.team, "entry", 25, source="entry")
        scoring_service.award(self.team, "main", 30, source="judge")
        scoring_service.award(self.team, "documentation", 20, source="judge")
        self.assertEqual(scoring_service.recompute(self.team).total, Decimal("75"))


class FindingScoringTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.red = red_team(self.comp)
        self.other = red_team(self.comp, "Chui")
        self.target = blue_team(self.comp)
        TargetAssignment.objects.create(
            competition=self.comp, red_team=self.red, target_team=self.target, is_active=True
        )

    def file(self, team, title, status=Finding.Status.VALID, severity="high", **kwargs):
        now = timezone.now()
        return Finding.objects.create(
            competition=self.comp, red_team=team, target_team=self.target,
            round=1, started_at=now, ended_at=now, title=title,
            technique="IDOR", steps="1. do it", evidence="log", impact="data",
            status=status, severity=severity, **kwargs,
        )

    def test_a_valid_finding_scores_the_team(self):
        self.file(self.red, "Auth bypass")
        scoring_service.award_for_findings(self.red)
        score = TeamScore.objects.get(team=self.red)
        self.assertGreater(score.main, 0)

    def test_an_invalid_finding_scores_nothing(self):
        self.file(self.red, "Not a bug", status=Finding.Status.INVALID, severity="")
        scoring_service.award_for_findings(self.red)
        marks = {e.criterion: e.marks for e in ScoreEvent.objects.filter(team=self.red)}
        self.assertEqual(marks["valid"], Decimal("0"))
        self.assertEqual(marks["severity"], Decimal("0"))

    def test_severity_drives_the_severity_mark(self):
        self.file(self.red, "Critical", severity="critical")
        scoring_service.award_for_findings(self.red)
        high_only = ScoreEvent.objects.get(team=self.red, criterion="severity").marks
        self.assertEqual(high_only, Decimal("5"))

    def test_a_downgrade_takes_marks_away(self):
        """The whole point of recomputing rather than adding."""
        finding = self.file(self.red, "Critical", severity="critical")
        scoring_service.award_for_findings(self.red)
        before = ScoreEvent.objects.get(team=self.red, criterion="severity").marks
        finding.severity = "low"
        finding.save()
        scoring_service.award_for_findings(self.red)
        after = ScoreEvent.objects.get(team=self.red, criterion="severity").marks
        self.assertLess(after, before)

    def test_the_first_report_scores_and_the_duplicate_does_not(self):
        """"First valid report wins a duplicate" — the published rule."""
        first = self.file(self.red, "Auth bypass")
        self.file(self.other, "Auth bypass again",
                  status=Finding.Status.DUPLICATE, severity="", duplicate_of=first)
        scoring_service.award_for_findings(self.red)
        scoring_service.award_for_findings(self.other)
        self.assertGreater(
            ScoreEvent.objects.get(team=self.red, criterion="valid").marks, 0
        )
        self.assertEqual(
            ScoreEvent.objects.get(team=self.other, criterion="valid").marks, Decimal("0")
        )

    def test_full_marks_are_reachable_without_racing_anybody(self):
        """A team finding issues nobody else found must still reach 40."""
        for index in range(8):
            self.file(self.red, f"Unique {index}", severity="critical")
        scoring_service.award_for_findings(self.red)
        self.assertEqual(scoring_service.recompute(self.red).main, Decimal("40"))

    def test_out_of_scope_costs_conduct_marks(self):
        self.file(self.red, "Wrong target", status=Finding.Status.OUT_OF_SCOPE, severity="")
        scoring_service.award_for_findings(self.red)
        self.assertEqual(
            ScoreEvent.objects.get(team=self.red, criterion="conduct").marks, Decimal("6")
        )

    def test_rescoring_is_idempotent(self):
        self.file(self.red, "Auth bypass")
        scoring_service.award_for_findings(self.red)
        first = scoring_service.recompute(self.red).main
        for _ in range(3):
            scoring_service.award_for_findings(self.red)
        self.assertEqual(scoring_service.recompute(self.red).main, first)
        self.assertEqual(ScoreEvent.objects.filter(team=self.red).count(), 3)

    def test_main_cannot_exceed_forty_however_many_findings(self):
        for index in range(40):
            self.file(self.red, f"Finding {index}", severity="critical")
        scoring_service.award_for_findings(self.red)
        self.assertEqual(scoring_service.recompute(self.red).main, Decimal("40"))

    def test_a_blue_team_is_not_scored_from_findings(self):
        scoring_service.award_for_findings(self.target)
        self.assertEqual(ScoreEvent.objects.filter(team=self.target).count(), 0)


class LeaderboardTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.a = blue_team(self.comp, "Alpha", cell="APP01")
        self.b = blue_team(self.comp, "Bravo", cell="APP02")
        self.c = blue_team(self.comp, "Charlie", cell="APP03")

    def test_it_orders_by_total(self):
        scoring_service.award(self.a, "documentation", 10, source="judge")
        scoring_service.award(self.b, "documentation", 25, source="judge")
        rows = scoring_service.leaderboard(self.comp, for_staff=True)
        self.assertEqual([r["team"] for r in rows[:2]], [self.b, self.a])

    def test_a_tie_shares_a_rank_and_the_next_one_skips(self):
        for team in (self.a, self.b):
            scoring_service.award(team, "documentation", 10, source="judge")
        rows = scoring_service.leaderboard(self.comp, for_staff=True)
        self.assertEqual([r["rank"] for r in rows], [1, 1, 3])

    def test_the_participant_board_is_genuinely_delayed(self):
        scoring_service.award(self.a, "documentation", 20, source="judge")
        live = scoring_service.leaderboard(self.comp, for_staff=True)
        delayed = scoring_service.leaderboard(self.comp, for_staff=False)
        self.assertEqual(next(r for r in live if r["team"] == self.a)["score"].total,
                         Decimal("20"))
        # Just-recorded marks are not in the delayed view yet.
        self.assertEqual(next(r for r in delayed if r["team"] == self.a)["score"].total,
                         Decimal("0"))

    def test_an_older_mark_does_reach_the_delayed_board(self):
        scoring_service.award(self.a, "documentation", 20, source="judge")
        ScoreEvent.objects.filter(team=self.a).update(
            recorded_at=timezone.now() - timezone.timedelta(hours=2)
        )
        rows = scoring_service.leaderboard(self.comp, for_staff=False)
        self.assertEqual(next(r for r in rows if r["team"] == self.a)["score"].total,
                         Decimal("20"))

    def test_a_withdrawn_team_is_not_on_the_board(self):
        self.c.status = Team.Status.WITHDRAWN
        self.c.save()
        rows = scoring_service.leaderboard(self.comp, for_staff=True)
        self.assertNotIn(self.c, [r["team"] for r in rows])


class ScorecardTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp)

    def test_an_unmarked_team_reads_as_pending(self):
        card = competition_service.scorecard(self.team)
        self.assertTrue(card["pending"])
        self.assertIsNone(card["total"])

    def test_a_marked_team_shows_its_marks(self):
        scoring_service.award(self.team, "documentation", 8, source="judge",
                              criterion="complete")
        card = competition_service.scorecard(self.team)
        self.assertFalse(card["pending"])
        self.assertEqual(card["total"], Decimal("8"))
        documentation = next(s for s in card["sections"] if s["name"] == "Documentation")
        row = next(r for r in documentation["rows"] if r["label"].startswith("The required"))
        self.assertEqual(row["score"], Decimal("8"))


class PageTests(TestCase):
    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp)
        self.rival = blue_team(self.comp, "Bravo", cell="APP02")
        self.staff = User.objects.create_user("op", password="x", is_staff=True,
                                              is_superuser=True)

    def test_the_board_needs_a_login(self):
        self.assertEqual(self.client.get(reverse("scoring:leaderboard")).status_code, 302)

    def test_a_competitor_sees_the_board(self):
        self.client.force_login(self.team.captain)
        response = self.client.get(reverse("scoring:leaderboard"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["live"])

    def test_a_marker_sees_it_live(self):
        self.client.force_login(self.staff)
        self.assertTrue(self.client.get(reverse("scoring:leaderboard")).context["live"])

    def test_a_team_can_read_every_mark_against_itself(self):
        scoring_service.award(self.team, "documentation", 8, source="judge",
                              criterion="complete", note="Security report was thin")
        self.client.force_login(self.team.captain)
        body = self.client.get(
            reverse("scoring:breakdown", args=[self.team.pk])
        ).content.decode()
        self.assertIn("Security report was thin", body)

    def test_a_team_cannot_read_another_team_s_marks(self):
        self.client.force_login(self.team.captain)
        self.assertEqual(
            self.client.get(reverse("scoring:breakdown", args=[self.rival.pk])).status_code,
            404,
        )

    def test_marking_is_for_markers_only(self):
        self.client.force_login(self.team.captain)
        self.assertNotEqual(
            self.client.get(reverse("scoring:mark", args=[self.team.pk])).status_code, 200
        )

    def test_a_judge_records_a_mark_through_the_screen(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("scoring:mark", args=[self.team.pk]), {
            "documentation:complete": "7",
            "note:documentation:complete": "Missing the AI-use statement",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TeamScore.objects.get(team=self.team).documentation, Decimal("7"))

    def test_a_mark_outside_the_range_is_refused(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("scoring:mark", args=[self.team.pk]), {
            "documentation:complete": "99",
        })
        self.assertEqual(ScoreEvent.objects.filter(team=self.team).count(), 0)

    def test_a_blank_field_leaves_the_mark_alone(self):
        scoring_service.award(self.team, "documentation", 5, source="judge",
                              criterion="complete", dedupe_key="judge:documentation:complete")
        self.client.force_login(self.staff)
        self.client.post(reverse("scoring:mark", args=[self.team.pk]),
                         {"documentation:complete": ""})
        self.assertEqual(
            ScoreEvent.objects.get(team=self.team, criterion="complete").marks, Decimal("5")
        )


class FeedViewTests(TestCase):
    """The venue scoreboard, and the delay it promises."""

    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp)
        self.staff = User.objects.create_user("op2", password="x", is_staff=True,
                                              is_superuser=True)

    def mark(self, note, *, minutes_ago=0, key=""):
        event = scoring_service.award(self.team, "documentation", 5, source="judge",
                                      criterion="complete", note=note, dedupe_key=key)
        if minutes_ago:
            ScoreEvent.objects.filter(pk=event.pk).update(
                recorded_at=timezone.now() - timezone.timedelta(minutes=minutes_ago)
            )
        return event

    def test_the_delay_is_five_minutes(self):
        self.assertEqual(scoring_service.LEADERBOARD_DELAY_MINUTES, 5)

    def test_a_marker_sees_a_mark_immediately(self):
        self.mark("Fresh")
        self.assertEqual(
            [e.note for e in scoring_service.feed(self.comp, for_staff=True)], ["Fresh"]
        )

    def test_a_participant_does_not_see_a_fresh_mark(self):
        self.mark("Fresh")
        self.assertEqual(scoring_service.feed(self.comp, for_staff=False), [])

    def test_a_participant_sees_it_once_the_delay_has_passed(self):
        self.mark("Older", minutes_ago=10, key="older")
        self.assertEqual(
            [e.note for e in scoring_service.feed(self.comp, for_staff=False)], ["Older"]
        )

    def test_the_page_refreshes_itself(self):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("scoring:live")), "http-equiv")

    def test_the_page_says_whose_clock_it_shows(self):
        self.client.force_login(self.team.captain)
        body = self.client.get(reverse("scoring:live")).content.decode()
        self.assertIn("5 minutes", body)

    def test_the_live_board_needs_a_login(self):
        self.assertEqual(self.client.get(reverse("scoring:live")).status_code, 302)


class FeedIngestTests(TestCase):
    """What observability pushes, and what happens when it misses."""

    def setUp(self):
        self.comp = make_competition()
        self.team = blue_team(self.comp, "Alpha", cell="APP01")
        self.team.team_identifier = "JS26-B-014"
        self.team.save()
        self.url = reverse("api_feed")

    def post(self, payload, token="a-long-enough-feed-token"):
        return self.client.post(
            self.url, json.dumps(payload), "application/json",
            headers={"x-feed-token": token},
        )

    def test_unconfigured_the_endpoint_does_not_exist(self):
        with override_settings(FEED_TOKEN=""):
            self.assertEqual(self.post({"scores": []}).status_code, 404)

    @override_settings(FEED_TOKEN="a-long-enough-feed-token")
    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.post({"scores": []}, token="wrong").status_code, 403)

    @override_settings(FEED_TOKEN="a-long-enough-feed-token")
    def test_a_mark_pushed_by_cell_lands_on_the_right_team(self):
        response = self.post({"competition": self.comp.pk, "scores": [
            {"cell": "enta-app01", "stage": "main", "criterion": "availability",
             "marks": 11, "note": "99.4% uptime", "key": "uptime:day1"},
        ]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accepted"], 1)
        self.assertEqual(TeamScore.objects.get(team=self.team).main, Decimal("11"))

    @override_settings(FEED_TOKEN="a-long-enough-feed-token")
    def test_a_mark_pushed_by_team_identifier_also_lands(self):
        self.post({"competition": self.comp.pk, "scores": [
            {"team": "JS26-B-014", "stage": "documentation", "marks": 7},
        ]})
        self.assertEqual(TeamScore.objects.get(team=self.team).documentation, Decimal("7"))

    @override_settings(FEED_TOKEN="a-long-enough-feed-token")
    def test_republishing_the_same_key_replaces_rather_than_adds(self):
        """An uptime figure is pushed again every few minutes."""
        for value in (9, 11, 10):
            self.post({"competition": self.comp.pk, "scores": [
                {"cell": "enta-app01", "stage": "main", "criterion": "availability",
                 "marks": value, "key": "uptime:day1"},
            ]})
        self.assertEqual(ScoreEvent.objects.filter(team=self.team).count(), 1)
        self.assertEqual(TeamScore.objects.get(team=self.team).main, Decimal("10"))

    @override_settings(FEED_TOKEN="a-long-enough-feed-token")
    def test_an_unmatched_cell_is_reported_not_dropped(self):
        response = self.post({"competition": self.comp.pk, "scores": [
            {"cell": "entb-app09", "stage": "main", "marks": 5},
        ]})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["accepted"], 0)
        self.assertIn("no team matched", response.json()["rejected"][0]["error"])

    @override_settings(FEED_TOKEN="a-long-enough-feed-token")
    def test_one_bad_row_does_not_lose_the_good_ones(self):
        response = self.post({"competition": self.comp.pk, "scores": [
            {"cell": "enta-app01", "stage": "main", "marks": 6, "key": "a"},
            {"cell": "nowhere-app99", "stage": "main", "marks": 6},
            {"cell": "enta-app01", "stage": "documentation", "marks": 4, "key": "b"},
        ]})
        self.assertEqual(response.json()["accepted"], 2)
        self.assertEqual(len(response.json()["rejected"]), 1)
