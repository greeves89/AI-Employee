"""Tests for the A/B improvement-loop plateau detection (issue #210).

Covers the two acceptance-criteria tests:
  - test_plateau_detection_after_3_no_improvement
  - test_single_dimension_change_recommendation
"""

import unittest

from app.services.skill_plateau import (
    _CONTROL_DIMENSIONS,
    _EXPRESSIVE_DIMENSIONS,
    is_plateaued,
    is_single_dimension_change,
    recommend_alternative_strategy,
)


class PlateauDetectionTests(unittest.TestCase):
    def test_plateau_detection_after_3_no_improvement(self):
        """3 consecutive versions stuck at the same rating → plateau."""
        # daily-standup skill: v2-v4 all at 2.4/5
        self.assertTrue(is_plateaued([2.2, 2.4, 2.4, 2.4]))
        self.assertTrue(is_plateaued([2.4, 2.4, 2.4]))

    def test_real_improvement_is_not_plateau(self):
        self.assertFalse(is_plateaued([2.2, 2.6, 3.0]))
        self.assertFalse(is_plateaued([2.0, 2.4, 2.9]))

    def test_too_few_versions_is_not_plateau(self):
        self.assertFalse(is_plateaued([2.4, 2.4]))
        self.assertFalse(is_plateaued([2.4]))
        self.assertFalse(is_plateaued([]))

    def test_regression_counts_as_no_improvement(self):
        # Getting worse is also "no improvement" → still triggers a rethink.
        self.assertTrue(is_plateaued([2.8, 2.5, 2.4]))

    def test_none_values_are_ignored(self):
        self.assertTrue(is_plateaued([None, 2.4, 2.4, 2.4]))
        self.assertFalse(is_plateaued([None, 2.4, 2.4]))  # only 2 real points

    def test_custom_window_and_delta(self):
        self.assertTrue(is_plateaued([3.0, 3.0], window=2, min_delta=0.2))
        self.assertFalse(is_plateaued([3.0, 3.3], window=2, min_delta=0.2))


class SingleDimensionRecommendationTests(unittest.TestCase):
    def test_single_dimension_change_recommendation(self):
        """After hammering the control cluster, recommend the opposite cluster."""
        rec = recommend_alternative_strategy(["examples", "rules", "templates"])

        # Must not recommend an already-exhausted dimension.
        self.assertNotIn(rec["recommended_dimension"], {"examples", "rules", "templates"})
        # Should steer toward the expressive cluster (the "try LESS control" lesson).
        self.assertIn(rec["recommended_dimension"], _EXPRESSIVE_DIMENSIONS)
        # And it must be applied as a single-dimension change.
        self.assertTrue(rec["single_dimension_constraint"])
        self.assertTrue(is_single_dimension_change([rec["recommended_dimension"]]))

    def test_recommends_untried_dimension_when_mixed(self):
        rec = recommend_alternative_strategy(["examples", "tone"])
        self.assertNotIn(rec["recommended_dimension"], {"examples", "tone"})
        self.assertIn(rec["recommended_dimension"], set(_CONTROL_DIMENSIONS) | set(_EXPRESSIVE_DIMENSIONS))

    def test_single_dimension_constraint_helper(self):
        self.assertTrue(is_single_dimension_change(["examples"]))
        self.assertTrue(is_single_dimension_change([]))
        self.assertFalse(is_single_dimension_change(["examples", "rules"]))


if __name__ == "__main__":
    unittest.main()
