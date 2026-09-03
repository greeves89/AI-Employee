"""Ausweichmodelle bei Ausfall — und wann man besser NICHT ausweicht (#200).

Der inhaltsbewusste Router wählt das Modell vor dem Lauf. Fehlte bisher: der Fall
danach. Antwortet das Modell nicht — Rate-Limit, Zeitüberschreitung, Überlastung,
Wartungsfenster einer Azure-Bereitstellung — brach der Lauf ab, und der Auftrag
stand auf „error", obwohl ein anderes Modell ihn hätte lösen können.

Der wichtigere Teil ist aber die andere Richtung: Bei einem falschen Schlüssel
oder einem nicht existierenden Bereitstellungsnamen hilft kein zweites Modell. Die
Kette würde denselben Fehler nur drei Mal teurer wiederholen **und die Ursache
verdecken**. Konfigurationsfehler müssen laut und sofort scheitern.
"""

import unittest

from app import model_fallback as mf


class RetryableTests(unittest.TestCase):
    def test_capacity_problems_are_worth_a_switch(self):
        for text in (
            "429 Too Many Requests",
            "Rate limit reached for gpt-5.6-terra",
            "Request timed out after 60s",
            "The engine is currently overloaded, please try again",
            "503 Service Unavailable",
            "upstream server_error",
        ):
            with self.subTest(text):
                self.assertTrue(mf.is_retryable(text))

    def test_setup_errors_are_not(self):
        for text in (
            "401 Unauthorized",
            "Invalid API key provided",
            "DeploymentNotFound: the deployment does not exist",
            "403 Forbidden",
        ):
            with self.subTest(text):
                self.assertFalse(mf.is_retryable(text))

    def test_a_setup_error_wins_over_a_capacity_word(self):
        """„401 Unauthorized, please try again later" ist KEIN Kapazitätsproblem.
        Sonst probiert die Kette alle Modelle mit demselben kaputten Schlüssel."""
        self.assertFalse(mf.is_retryable("401 Unauthorized — please try again later"))

    def test_content_filter_does_not_switch(self):
        """Eine Inhaltsablehnung folgt der Richtlinie des Anbieters, nicht seiner
        Auslastung — ein anderes Modell umgeht sie bestenfalls zufaellig."""
        self.assertFalse(mf.is_retryable("Blocked by our content filters"))

    def test_unknown_errors_do_not_switch(self):
        """Im Zweifel nicht. Ein unbekannter Fehler wird durch Wiederholen nicht
        besser — er wird nur teurer und schwerer zu finden."""
        self.assertFalse(mf.is_retryable("Etwas ist schiefgelaufen"))
        self.assertFalse(mf.is_retryable(""))
        self.assertFalse(mf.is_retryable(None))


class ChainTests(unittest.TestCase):
    def test_parsing_is_forgiving(self):
        self.assertEqual(mf.parse_chain(" a, b ,, c "), ["a", "b", "c"])
        self.assertEqual(mf.parse_chain(""), [])
        self.assertEqual(mf.parse_chain(None), [])

    def test_duplicates_are_dropped(self):
        self.assertEqual(mf.parse_chain("a,b,a"), ["a", "b"])

    def test_the_current_model_is_skipped(self):
        """Wer sein Hauptmodell auch in die Ausweichliste schreibt, soll nicht
        auf dasselbe ausfallende Modell umgestellt werden."""
        self.assertEqual(mf.next_model("a", ["a", "b"]), "b")

    def test_already_tried_models_are_skipped(self):
        self.assertEqual(mf.next_model("b", ["a", "b", "c"], {"a"}), "c")

    def test_an_exhausted_chain_gives_up(self):
        self.assertIsNone(mf.next_model("c", ["a", "b", "c"], {"a", "b"}))

    def test_no_chain_means_no_fallback(self):
        self.assertIsNone(mf.next_model("a", []))

    def test_the_chain_keeps_its_order(self):
        """Die Reihenfolge ist eine Entscheidung des Betreibers — meist gutes
        Modell zuerst, billiges zuletzt."""
        self.assertEqual(mf.next_model("x", ["gross", "mittel", "klein"]), "gross")


class BothRuntimesHaveItTests(unittest.TestCase):
    """Auftragslauf UND Chat. Eine Laufzeit ohne Ausfallsicherheit waere genau die
    Luecke, die in diesem Projekt schon mehrfach zugeschlagen hat — und im Chat
    merkt der Mensch den Ausfall sofort."""

    def test_the_runner_has_the_switch(self):
        from app.llm_runner import LLMRunner

        self.assertTrue(hasattr(LLMRunner, "_switch_to_fallback"))

    def test_the_chat_handler_has_it_too(self):
        from app.llm_chat_handler import LLMChatHandler

        self.assertTrue(hasattr(LLMChatHandler, "_switch_to_fallback"))

    def test_both_read_the_same_setting(self):
        from app.config import Settings

        self.assertIn("llm_fallback_models", Settings.model_fields)


if __name__ == "__main__":
    unittest.main()
