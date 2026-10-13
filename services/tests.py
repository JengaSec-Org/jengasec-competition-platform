from django.test import SimpleTestCase

from services.scoring_service import (
    classify_submission_type,
    final_team_score,
    weighted_score,
)


class ScoringMathTests(SimpleTestCase):
    def test_weighted_score_fractions(self):
        rows = [
            {"score": 80, "max": 100, "weight": 0.5},
            {"score": 60, "max": 100, "weight": 0.5},
        ]
        self.assertEqual(weighted_score(rows), 70.0)

    def test_weighted_score_percent_weights(self):
        rows = [
            {"score": 30, "max": 30, "weight": 50},
            {"score": 0, "max": 30, "weight": 50},
        ]
        self.assertEqual(weighted_score(rows), 50.0)

    def test_weighted_score_errors(self):
        with self.assertRaises(ValueError):
            weighted_score([])
        with self.assertRaises(ValueError):
            weighted_score([{"score": 1, "max": 0, "weight": 1}])
        with self.assertRaises(ValueError):
            weighted_score([{"score": 1, "max": 10, "weight": -1}])

    def test_final_team_score(self):
        s = {"proposal": 80, "documentation": 70, "report": 90}
        w = {"proposal": 20, "documentation": 50, "report": 30}
        self.assertEqual(final_team_score(s, w), 78.0)

    def test_final_team_score_errors(self):
        with self.assertRaises(ValueError):
            final_team_score({}, {})
        with self.assertRaises(ValueError):
            final_team_score({"a": 1}, {})

    def test_classify(self):
        self.assertEqual(classify_submission_type("Proposal"), "proposal")
        self.assertEqual(classify_submission_type("Blue Team Final Documentation"), "documentation")
        self.assertEqual(classify_submission_type("Red Team Report"), "report")
        self.assertIsNone(classify_submission_type("Other"))