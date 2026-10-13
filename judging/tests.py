from django.contrib.auth import get_user_model
from django.test import TestCase

from judging.models import CriterionScore, Evaluation
from rubrics.models import Rubric, RubricCriterion

User = get_user_model()


class OverrideTests(TestCase):
    """Needs a Submission fixture; adapt the setUp lines to your factories."""

    def test_override_requires_reason(self):
        cs = CriterionScore(final_score=5)
        with self.assertRaises(ValueError):
            CriterionScore.apply_judge_score(cs, 7, judge=None, reason="")