"""Policy boundaries that can change a competition result."""
from decimal import Decimal
from django.test import SimpleTestCase

from jengasec_scoring_policy import PolicyError, calculate, digest

QUALITY = {"receipts_complete": True, "source_authenticated": True, "clock_uncertainty_seconds": 0}


class PolicyTests(SimpleTestCase):
    def test_all_main_formulas(self):
        cases = [
            ("application_blue", "availability", {"expected": 100, "completed": 99, "successful": 90}, "10.91"),
            ("application_blue", "detected", {"attacks": 5, "detected": 3}, "7.20"),
            ("application_blue", "response", {"required": 4, "passed": 3}, "7.50"),
            ("application_blue", "unfound", {"eligible": 4, "protected": 3}, "4.50"),
            ("application_red", "valid", {"valid_findings": 20}, "16.00"),
            ("application_red", "severity", {"critical": 1, "high": 1, "medium": 1, "low": 1}, "11.00"),
            ("application_red", "conduct", {"out_of_scope": 10}, "0.00"),
            ("ai_blue", "precision", {"tp": 2, "fp": 1, "delivered_attacks": 3}, "8.00"),
            ("ai_blue", "recall", {"detection_seconds": [0, 30, None, 70]}, "4.50"),
            ("ai_blue", "correlation", {"pair_tp": 2, "pair_fp": 1, "pair_fn": 1, "hybrid_passed": 3, "hybrid_required": 4}, "5.67"),
            ("ai_blue", "decisions", {"required": 4, "context": 4, "evidence": 3, "consistency": 2}, "4.50"),
            ("ai_blue", "safety", {"scope": True, "budget": True, "reversal": False, "kill_switch": True}, "3.00"),
            ("ai_red", "missed", {"objective_ids": [f"case-{n}" for n in range(20)]}, "16.00"),
            ("ai_red", "false", {"objective_ids": ["case-a", "case-a"]}, "2.00"),
            ("ai_red", "breadth", {"families": ["injection", "evasion", "evasion"]}, "4.00"),
        ]
        for side, criterion, facts, expected in cases:
            with self.subTest(side=side, criterion=criterion):
                result = calculate(side, criterion, facts, QUALITY)
                self.assertEqual((result.status, result.marks), ("ready", Decimal(expected)))

    def test_no_observation_is_pending_and_measured_failure_is_zero(self):
        for expected, completed, successful, status, marks in (
            (100, 98, 98, "pending", None), (0, 0, 0, "pending", None),
            (100, 100, 0, "ready", Decimal("0.00")),
        ):
            result = calculate("application_blue", "availability", {
                "expected": expected, "completed": completed, "successful": successful}, QUALITY)
            self.assertEqual((result.status, result.marks), (status, marks))
        for attacks, state in ((0, "pending"), (3, "ready")):
            result = calculate("ai_blue", "precision", {"tp": 0, "fp": 0, "delivered_attacks": attacks}, QUALITY)
            self.assertEqual(result.status, state)
            if attacks:
                self.assertEqual(result.marks, 0)

    def test_missing_receipts_authentication_and_uncertain_clocks_hold_publication(self):
        facts = {"attacks": 1, "detected": 1}
        for key, value in (("receipts_complete", False), ("source_authenticated", False),
                           ("clock_uncertainty_seconds", 2.01)):
            with self.subTest(key=key):
                result = calculate("application_blue", "detected", facts, {**QUALITY, key: value})
                self.assertEqual(result.status, "pending")

    def test_invalid_facts_are_rejected(self):
        cases = [
            ("application_blue", "detected", {"attacks": 1, "detected": 2}),
            ("application_blue", "detected", {"attacks": True, "detected": 0}),
            ("application_blue", "detected", {"attacks": -1, "detected": 0}),
            ("application_blue", "detected", {"attacks": 1, "detected": 1, "extra": 0}),
            ("ai_blue", "recall", {"detection_seconds": [float("nan")]}),
            ("ai_blue", "recall", {"detection_seconds": [-1]}),
            ("ai_blue", "precision", {"tp": 2, "fp": 0, "delivered_attacks": 1}),
            ("ai_blue", "safety", {"scope": 1, "budget": True, "reversal": True, "kill_switch": True}),
            ("ai_red", "breadth", {"families": ["unknown"]}),
        ]
        for side, criterion, facts in cases:
            with self.subTest(side=side, criterion=criterion, facts=facts):
                with self.assertRaises(PolicyError):
                    calculate(side, criterion, facts, QUALITY)

    def test_policy_versions_and_hashes_are_exact(self):
        with self.assertRaises(PolicyError):
            calculate("ai_red", "missed", {"objective_ids": []}, QUALITY, policy_version="future")
        self.assertEqual(digest({"b": 2, "a": 1}), digest({"a": 1, "b": 2}))
        with self.assertRaises(PolicyError):
            digest({"value": float("inf")})
