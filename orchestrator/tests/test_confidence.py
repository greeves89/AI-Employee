"""Konfidenz-Routing (#389) — wann ein Mensch geholt wird.

Zwei Fehler waeren teuer und beide leise:

* Eine falsch gelesene Zahl. Modelle liefern mal ``0.9``, mal ``90``, mal ``"85%"``.
  Ohne Umrechnung waere ``0.9`` eine Konfidenz von einem Prozent — also eine
  Eskalation bei nahezu vollstaendiger Sicherheit, bei jedem einzelnen Aufruf.
* Eine Schwelle, die nie greift. Dann meldet der Agent brav seine Unsicherheit,
  und niemand erfaehrt davon.
"""

import unittest

from app.core.confidence import (
    DEFAULT_THRESHOLD,
    build_question,
    is_enabled,
    normalize_confidence,
    should_escalate,
    threshold_for,
)


class NormalizeTests(unittest.TestCase):
    def test_percent_scale_passes_through(self):
        self.assertEqual(normalize_confidence(85), 85)
        self.assertEqual(normalize_confidence(85.4), 85)
        self.assertEqual(normalize_confidence(100), 100)

    def test_fraction_scale_is_converted(self):
        """Der teuerste Einzelfehler: 0.9 als 1 % zu lesen."""
        self.assertEqual(normalize_confidence(0.9), 90)
        self.assertEqual(normalize_confidence(0.4), 40)
        self.assertEqual(normalize_confidence(0), 0)

    def test_one_means_certain_not_one_percent(self):
        self.assertEqual(normalize_confidence(1), 100)
        self.assertEqual(normalize_confidence(1.0), 100)

    def test_text_forms(self):
        self.assertEqual(normalize_confidence("85"), 85)
        self.assertEqual(normalize_confidence("85%"), 85)
        self.assertEqual(normalize_confidence(" 0,9 "), 90)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(normalize_confidence(140), 100)
        self.assertEqual(normalize_confidence(-20), 0)

    def test_garbage_is_refused_not_guessed(self):
        for bad in (None, "", "ziemlich sicher", float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    normalize_confidence(bad)


class ThresholdTests(unittest.TestCase):
    def test_default_when_nothing_set(self):
        self.assertEqual(threshold_for(None), DEFAULT_THRESHOLD)
        self.assertEqual(threshold_for({}), DEFAULT_THRESHOLD)

    def test_agent_overrides_default(self):
        self.assertEqual(threshold_for({"confidence": {"threshold": 90}}), 90)

    def test_task_overrides_agent(self):
        """Eine Abrechnung vertraegt weniger Raten als eine Zusammenfassung."""
        self.assertEqual(
            threshold_for({"confidence": {"threshold": 50}}, {"confidence_threshold": 95}),
            95,
        )

    def test_unreadable_value_falls_through_instead_of_disabling(self):
        """Ein kaputter Wert darf nicht stillschweigend „nie fragen" bedeuten."""
        self.assertEqual(
            threshold_for({"confidence": {"threshold": "hoch"}}), DEFAULT_THRESHOLD
        )
        self.assertEqual(
            threshold_for({"confidence": {"threshold": 90}}, {"confidence_threshold": "?"}),
            90,
        )

    def test_out_of_range_is_clamped(self):
        self.assertEqual(threshold_for({"confidence": {"threshold": 500}}), 100)
        self.assertEqual(threshold_for({"confidence": {"threshold": -5}}), 0)


class EnabledTests(unittest.TestCase):
    def test_on_by_default(self):
        """Eine Sicherung, die man erst einschalten muss, bleibt aus."""
        self.assertTrue(is_enabled(None))
        self.assertTrue(is_enabled({}))

    def test_can_be_switched_off(self):
        self.assertFalse(is_enabled({"confidence": {"enabled": False}}))
        self.assertFalse(is_enabled({"confidence": {"enabled": "false"}}))
        self.assertTrue(is_enabled({"confidence": {"enabled": "true"}}))


class DecisionTests(unittest.TestCase):
    def test_below_threshold_escalates(self):
        self.assertTrue(should_escalate(40, 70))

    def test_at_threshold_proceeds(self):
        """Sonst hiesse eine Schwelle von 100, dass auch vollstaendige Sicherheit
        noch eine Rueckfrage ausloest."""
        self.assertFalse(should_escalate(70, 70))
        self.assertFalse(should_escalate(100, 100))

    def test_threshold_zero_never_asks(self):
        self.assertFalse(should_escalate(0, 0))

    def test_question_carries_the_reason(self):
        text = build_question("Welche Kostenstelle?", 40, 70)
        self.assertIn("40%", text)
        self.assertIn("70%", text)
        self.assertIn("Welche Kostenstelle?", text)


if __name__ == "__main__":
    unittest.main()
