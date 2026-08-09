"""Golden-Tests: die Rechnung (#391).

Ein Gatter, dessen Bewertung schwankt, blockiert mal und laesst mal durch — und
dann glaubt ihm zu Recht niemand mehr. Diese Tests halten fest, dass die Bewertung
reproduzierbar ist und dass die zwei Faelle, in denen man sich am leichtesten selbst
belegt, wirklich auffallen: eine Aufgabe ohne Erwartung und ein kaputtes Muster.
"""

import unittest

from app.core.eval_harness import (
    DEFAULT_TOLERANCE,
    check_item,
    gate_decision,
    is_regression,
    score_results,
    validate_items,
)


class CheckItemTests(unittest.TestCase):
    def test_contains_is_case_insensitive(self):
        r = check_item({"expect_contains": ["Umsatzsteuer"]}, "Die UMSATZSTEUER betraegt 19%.")
        self.assertTrue(r["ok"])

    def test_absent_catches_the_forbidden_sentence(self):
        r = check_item({"expect_absent": ["ich kann das nicht"]}, "Ich kann das nicht beurteilen.")
        self.assertFalse(r["ok"])

    def test_regex(self):
        r = check_item({"expect_regex": [r"\d+,\d{2}\s*€"]}, "Summe: 1234,00 €")
        self.assertTrue(r["ok"])

    def test_broken_regex_fails_instead_of_passing(self):
        """Ein Tippfehler in der Sammlung darf keine echte Verschlechterung
        verdecken."""
        r = check_item({"expect_regex": ["([unbalanced"]}, "egal")
        self.assertFalse(r["ok"])
        self.assertIn("error", r["checks"][0])

    def test_item_without_expectation_never_passes(self):
        """Sonst zoege eine leere Aufgabe den Wert stillschweigend nach oben."""
        r = check_item({}, "irgendeine Antwort")
        self.assertFalse(r["ok"])
        self.assertEqual(r["checks"][0]["kind"], "none")

    def test_missing_answer_is_a_failure_not_a_skip(self):
        r = check_item({"expect_contains": ["x"]}, None)
        self.assertFalse(r["ok"])

    def test_min_length(self):
        self.assertFalse(check_item({"min_length": 50}, "zu kurz")["ok"])
        self.assertTrue(check_item({"min_length": 3}, "lang genug")["ok"])

    def test_reason_is_always_recorded(self):
        """Ein Fehlschlag ohne Begruendung zwingt jemanden, von Hand nachzustellen,
        was eigentlich erwartet war."""
        r = check_item({"expect_contains": ["a", "b"]}, "nur a")
        self.assertEqual(len(r["checks"]), 2)
        self.assertTrue(r["checks"][0]["ok"])
        self.assertFalse(r["checks"][1]["ok"])

    def test_deterministic(self):
        item = {"expect_contains": ["x"], "expect_regex": [r"\d"]}
        first = check_item(item, "x1")
        for _ in range(20):
            self.assertEqual(check_item(item, "x1"), first)


class ScoreTests(unittest.TestCase):
    def test_weighted(self):
        results = [{"ok": True, "weight": 1}, {"ok": False, "weight": 3}]
        self.assertEqual(score_results(results), 25.0)

    def test_all_passed(self):
        self.assertEqual(score_results([{"ok": True, "weight": 1}]), 100.0)

    def test_empty_is_zero_not_perfect(self):
        """Ein leerer Lauf darf nicht als bestanden gelten."""
        self.assertEqual(score_results([]), 0.0)

    def test_nonsense_weight_counts_as_one(self):
        from app.core.eval_harness import _weight
        self.assertEqual(_weight({"weight": "viel"}), 1.0)
        self.assertEqual(_weight({"weight": 0}), 1.0)
        self.assertEqual(_weight({"weight": -5}), 1.0)


class RegressionTests(unittest.TestCase):
    def test_first_run_is_never_a_regression(self):
        """Sonst koennte niemand je anfangen."""
        self.assertFalse(is_regression(10.0, None))

    def test_small_dip_is_tolerated(self):
        self.assertFalse(is_regression(96.0, 100.0))

    def test_real_drop_is_caught(self):
        self.assertTrue(is_regression(80.0, 100.0))

    def test_improvement_is_never_a_regression(self):
        self.assertFalse(is_regression(100.0, 80.0))

    def test_tolerance_boundary(self):
        self.assertFalse(is_regression(100.0 - DEFAULT_TOLERANCE, 100.0))
        self.assertTrue(is_regression(100.0 - DEFAULT_TOLERANCE - 0.1, 100.0))


class GateTests(unittest.TestCase):
    def test_no_tests_does_not_block_by_default(self):
        """Ein Gatter, das jedes Update blockiert, wird binnen einer Woche
        abgeschaltet und schuetzt dann gar nichts mehr."""
        d = gate_decision(score=None, baseline=None)
        self.assertTrue(d["allowed"])

    def test_no_tests_blocks_when_required(self):
        d = gate_decision(score=None, baseline=None, require_run=True)
        self.assertFalse(d["allowed"])
        self.assertEqual(d["reason"], "no_run")

    def test_regression_blocks(self):
        d = gate_decision(score=70.0, baseline=95.0)
        self.assertFalse(d["allowed"])
        self.assertEqual(d["reason"], "regression")
        self.assertIn("70", d["message"])
        self.assertIn("95", d["message"])

    def test_first_run_passes_and_says_so(self):
        d = gate_decision(score=80.0, baseline=None)
        self.assertTrue(d["allowed"])
        self.assertEqual(d["reason"], "first_run")

    def test_stable_score_passes(self):
        self.assertTrue(gate_decision(score=97.0, baseline=100.0)["allowed"])


class ValidateTests(unittest.TestCase):
    def test_minimal_valid_set(self):
        items = validate_items([{"prompt": "Rechne 19% aus", "expect_contains": ["19"]}])
        self.assertEqual(items[0]["id"], "i1")
        self.assertEqual(items[0]["weight"], 1.0)

    def test_empty_set_refused(self):
        with self.assertRaises(ValueError):
            validate_items([])

    def test_item_without_prompt_refused(self):
        with self.assertRaises(ValueError):
            validate_items([{"expect_contains": ["x"]}])

    def test_item_without_expectation_refused(self):
        """Eine Aufgabe, die nie durchfallen kann, ist kein Test."""
        with self.assertRaises(ValueError):
            validate_items([{"prompt": "Tu was"}])

    def test_duplicate_ids_refused(self):
        """Doppelte Kennungen wuerden beim Zuordnen die falsche Aufgabe treffen —
        und der Wert waere still falsch."""
        with self.assertRaises(ValueError):
            validate_items([
                {"id": "a", "prompt": "x", "expect_contains": ["x"]},
                {"id": "a", "prompt": "y", "expect_contains": ["y"]},
            ])

    def test_broken_regex_refused_at_save_time(self):
        with self.assertRaises(ValueError):
            validate_items([{"prompt": "x", "expect_regex": ["([unbalanced"]}])

    def test_non_list_expectation_refused(self):
        with self.assertRaises(ValueError):
            validate_items([{"prompt": "x", "expect_contains": "nicht-liste"}])

    def test_too_many_items_refused(self):
        with self.assertRaises(ValueError):
            validate_items([{"prompt": "x", "expect_contains": ["x"]}] * 101)


if __name__ == "__main__":
    unittest.main()
