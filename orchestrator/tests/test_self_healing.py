"""Selbstheilung (#390) — wann wiederholt wird und wann eben nicht.

Der teuerste Fehler waere ein Regelwerk, das dauerhafte Fehler wiederholt: drei
Laeufe gegen ein falsches Kennwort kosten Geld und aendern nichts. Der zweitteuerste
waere eines, das voruebergehende NICHT wiederholt — dann weckt ein Zeitablauf um
drei Uhr morgens einen Menschen.
"""

import unittest

from app.core.self_healing import (
    DEFAULT_POLICY,
    PERMANENT,
    STRATEGY_DECOMPOSE,
    STRATEGY_OTHER_MODEL,
    STRATEGY_RETRY,
    TRANSIENT,
    UNKNOWN,
    build_retry_prompt,
    classify_error,
    delay_for_attempt,
    escalation_summary,
    plan_next_attempt,
    policy_for,
    strategy_for_attempt,
)


class ClassifyTests(unittest.TestCase):
    def test_transient_errors(self):
        for err in (
            "Request timed out after 600s",
            "HTTP 429 Too Many Requests",
            "Error 503 Service Unavailable",
            "anthropic: Overloaded",
            "Connection reset by peer",
            "upstream connect error: connection refused",
            "Read timeout",
            "502 Bad Gateway",
            "Temporary failure in name resolution",
        ):
            with self.subTest(err=err):
                self.assertEqual(classify_error(err), TRANSIENT)

    def test_permanent_errors(self):
        for err in (
            "401 Unauthorized",
            "invalid api key provided",
            "permission denied: /root/.ssh",
            "Your credit balance is too low",
            "ModuleNotFoundError: No module named 'foo'",
            "model claude-2 does not exist",
            "SyntaxError: invalid syntax",
            "404 Not Found",
        ):
            with self.subTest(err=err):
                self.assertEqual(classify_error(err), PERMANENT)

    def test_permanent_wins_over_transient(self):
        """„503 — invalid api key" liest sich vorne wie ein Ausfall, ist keiner.
        Wiederholen wuerde den echten Grund nur verschleppen."""
        self.assertEqual(classify_error("503 Service Unavailable: invalid api key"), PERMANENT)

    def test_no_message_is_unknown_not_harmless(self):
        self.assertEqual(classify_error(None), UNKNOWN)
        self.assertEqual(classify_error("   "), UNKNOWN)
        self.assertEqual(classify_error("Something odd happened"), UNKNOWN)


class PolicyTests(unittest.TestCase):
    def test_defaults_when_nothing_configured(self):
        self.assertEqual(policy_for(None), policy_for({}))
        self.assertTrue(policy_for(None)["enabled"])

    def test_partial_override_keeps_the_rest(self):
        policy = policy_for({"self_healing": {"max_attempts": 1}})
        self.assertEqual(policy["max_attempts"], 1)
        self.assertEqual(policy["base_delay_seconds"], DEFAULT_POLICY["base_delay_seconds"])

    def test_absurd_values_are_reined_in(self):
        """Ein Zahlendreher darf einen Agenten nicht stundenlang gegen eine Wand
        laufen lassen."""
        policy = policy_for({"self_healing": {"max_attempts": 99, "base_delay_seconds": 0}})
        self.assertLessEqual(policy["max_attempts"], 5)
        self.assertGreaterEqual(policy["base_delay_seconds"], 5)

    def test_unknown_keys_are_ignored(self):
        policy = policy_for({"self_healing": {"nonsense": True}})
        self.assertNotIn("nonsense", policy)

    def test_switches_are_real_booleans(self):
        """Aus der Oberflaeche kann „false" als Text kommen — und ein nichtleerer
        Text ist wahr. „Aus" waere damit eingeschaltet."""
        self.assertFalse(policy_for({"self_healing": {"enabled": "false"}})["enabled"])
        self.assertFalse(policy_for({"self_healing": {"enabled": "0"}})["enabled"])
        self.assertFalse(policy_for({"self_healing": {"enabled": "nein"}})["enabled"])
        self.assertTrue(policy_for({"self_healing": {"enabled": "true"}})["enabled"])
        self.assertFalse(policy_for({"self_healing": {"retry_unknown": "off"}})["retry_unknown"])


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.policy = policy_for(None)

    def test_permanent_error_escalates_immediately(self):
        self.assertIsNone(
            plan_next_attempt(error="401 Unauthorized", attempt_so_far=0, policy=self.policy)
        )

    def test_transient_error_is_retried(self):
        plan = plan_next_attempt(error="timeout", attempt_so_far=0, policy=self.policy)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["attempt"], 1)
        self.assertEqual(plan["strategy"], STRATEGY_RETRY)

    def test_attempts_are_exhausted_and_then_escalate(self):
        for so_far in range(self.policy["max_attempts"]):
            self.assertIsNotNone(
                plan_next_attempt(error="timeout", attempt_so_far=so_far, policy=self.policy)
            )
        self.assertIsNone(
            plan_next_attempt(
                error="timeout",
                attempt_so_far=self.policy["max_attempts"],
                policy=self.policy,
            )
        )

    def test_disabled_never_retries(self):
        policy = policy_for({"self_healing": {"enabled": False}})
        self.assertIsNone(plan_next_attempt(error="timeout", attempt_so_far=0, policy=policy))

    def test_unknown_can_be_switched_off(self):
        strict = policy_for({"self_healing": {"retry_unknown": False}})
        self.assertIsNone(plan_next_attempt(error="???", attempt_so_far=0, policy=strict))
        self.assertIsNotNone(plan_next_attempt(error="timeout", attempt_so_far=0, policy=strict))

    def test_last_attempt_is_marked(self):
        plan = plan_next_attempt(
            error="timeout",
            attempt_so_far=self.policy["max_attempts"] - 1,
            policy=self.policy,
        )
        self.assertTrue(plan["is_last"])


class StrategyTests(unittest.TestCase):
    def test_first_retry_changes_nothing(self):
        """Bei einem Ausfall der Gegenstelle waere jede Aenderung am Auftrag nur
        Rauschen."""
        self.assertEqual(strategy_for_attempt(1), STRATEGY_RETRY)
        prompt = "Erstelle den Monatsbericht."
        self.assertEqual(build_retry_prompt(prompt, STRATEGY_RETRY, "timeout"), prompt)

    def test_second_retry_changes_the_approach(self):
        self.assertEqual(strategy_for_attempt(2), STRATEGY_DECOMPOSE)
        out = build_retry_prompt("Erstelle den Bericht.", STRATEGY_DECOMPOSE, "timeout")
        self.assertIn("Erstelle den Bericht.", out)
        self.assertIn("kleinere", out)
        self.assertIn("timeout", out)

    def test_third_retry_changes_the_model(self):
        self.assertEqual(strategy_for_attempt(3), STRATEGY_OTHER_MODEL)

    def test_error_excerpt_is_bounded(self):
        out = build_retry_prompt("x", STRATEGY_DECOMPOSE, "y" * 5000)
        self.assertLess(len(out), 1500)

    def test_missing_error_still_produces_a_usable_prompt(self):
        out = build_retry_prompt("x", STRATEGY_DECOMPOSE, None)
        self.assertIn("(keine Meldung)", out)


class DelayTests(unittest.TestCase):
    def test_delay_doubles_and_is_capped(self):
        policy = policy_for(None)
        first = delay_for_attempt(1, policy)
        second = delay_for_attempt(2, policy)
        self.assertEqual(second, first * 2)
        self.assertLessEqual(delay_for_attempt(20, policy), policy["max_delay_seconds"])

    def test_delay_is_never_zero(self):
        """Sofort neu zu versuchen trifft dieselbe ueberlastete Gegenstelle im
        selben Zustand."""
        policy = policy_for({"self_healing": {"base_delay_seconds": 0}})
        self.assertGreater(delay_for_attempt(1, policy), 0)


class EscalationTests(unittest.TestCase):
    def test_summary_carries_the_history(self):
        history = [
            {"attempt": 0, "strategy": "original", "classification": TRANSIENT, "error": "timeout"},
            {"attempt": 1, "strategy": STRATEGY_RETRY, "classification": TRANSIENT, "error": "503"},
        ]
        text = escalation_summary(history, "503 again")
        self.assertIn("Versuch 0", text)
        self.assertIn("Versuch 1", text)
        self.assertIn("503 again", text)

    def test_summary_without_history_still_names_the_error(self):
        text = escalation_summary([], "401 Unauthorized")
        self.assertIn("401 Unauthorized", text)

    def test_summary_handles_missing_error(self):
        self.assertIn("(keine Meldung)", escalation_summary([], None))


if __name__ == "__main__":
    unittest.main()
