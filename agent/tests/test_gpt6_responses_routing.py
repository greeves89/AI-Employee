"""Neue OpenAI-Modelle duerfen nicht an einer Namensliste scheitern.

Am 06.09.2026 wurde gpt-6-astra eingebunden und antwortete mit 400: "Function
tools with reasoning_effort are not supported ... in /v1/chat/completions. To
use function tools, use /v1/responses". Die Weiche kannte nur "gpt-5"/"codex"
und schickte das Modell auf den Chat-Weg — obwohl unser Provider dort gar
keine Denkstufe sendet: Das Modell denkt standardmaessig, und mit Werkzeugen
geht das nur ueber /v1/responses.

Zwei Sicherungen: die Liste kennt jetzt gpt-6, UND der Provider liest die
Antwort des Modells — sagt sie "/v1/responses", wird umgeleitet und gemerkt.
Damit haengt das naechste Modell nicht wieder an der Liste.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import openai_provider as op


def _provider(model: str):
    p = op.OpenAIProvider.__new__(op.OpenAIProvider)
    p.model_name = model
    p.model = model
    p.reasoning_effort = "high"
    p.temperature = 0.7
    p.max_tokens = None
    return p


class NamenslisteTests(unittest.TestCase):
    def test_gpt6_geht_auf_den_responses_weg(self):
        for name in ("gpt-6-astra", "gpt-6-sol", "gpt-6", "GPT-6-Astra"):
            with self.subTest(name=name):
                self.assertTrue(_provider(name)._is_responses_model())

    def test_gpt6_sendet_werkzeuge_mit_denkstufe(self):
        """Genau die Kombination, die auf dem Chat-Weg scheiterte."""
        p = _provider("gpt-6-astra")
        self.assertTrue(p._supports_reasoning_effort())
        self.assertFalse(p._supports_custom_temperature())

    def test_gpt4o_bleibt_auf_dem_chat_weg(self):
        self.assertFalse(_provider("gpt-4o")._is_responses_model())


class LaufzeitErkennungTests(unittest.TestCase):
    def setUp(self):
        op._BRAUCHT_RESPONSES.clear()

    def tearDown(self):
        op._BRAUCHT_RESPONSES.clear()

    ECHTE_MELDUNG = ('{"error": {"message": "Function tools with reasoning_effort '
                     'are not supported for gpt-7-nova in /v1/chat/completions. To use '
                     'function tools, use /v1/responses or set reasoning_effort to '
                     "'none'.\", \"type\": \"invalid_request_error\"}}")

    def test_die_echte_meldung_wird_erkannt(self):
        self.assertTrue(op._verlangt_responses_weg(self.ECHTE_MELDUNG))

    def test_ein_beliebiger_400_leitet_nicht_um(self):
        for text in ("maximum context length exceeded",
                     "temperature does not support 0.7",
                     "Rate limit reached", "", None):
            with self.subTest(text=text):
                self.assertFalse(op._verlangt_responses_weg(text))

    def test_ein_unbekanntes_modell_wird_nach_der_meldung_umgeleitet(self):
        """Das Modell, das es beim Schreiben der Liste noch nicht gab."""
        p = _provider("gpt-7-nova")
        self.assertFalse(p._is_responses_model())
        op._BRAUCHT_RESPONSES.add("gpt-7-nova")
        self.assertTrue(p._is_responses_model())

    def test_die_umleitung_gilt_nur_fuer_dieses_modell(self):
        op._BRAUCHT_RESPONSES.add("gpt-7-nova")
        self.assertFalse(_provider("gpt-4o")._is_responses_model())


class VerdrahtungTests(unittest.TestCase):
    QUELLE = (Path(__file__).resolve().parents[1] / "app" / "providers"
              / "openai_provider.py").read_text()

    def test_der_chat_weg_meldet_nach_oben(self):
        block = self.QUELLE.split("async def _stream_chat_with_body(", 1)[1][:2500]
        self.assertIn("_BRAUCHT_RESPONSES.add(self.model_name)", block)
        self.assertIn("raise _ResponsesWegNoetig", block)

    def test_oben_wird_der_responses_weg_genommen(self):
        block = self.QUELLE.split("async def _stream_chat(", 1)[1][:1800]
        self.assertIn("except _ResponsesWegNoetig", block)
        self.assertIn("self._stream_responses(neue_url, messages, tools)", block)
        # Kein Kreis: fuehrt _resolve_url weiter auf "chat", wird der Fehler
        # durchgereicht statt erneut versucht.
        self.assertIn('if fmt != "responses":', block)


if __name__ == "__main__":
    unittest.main()
