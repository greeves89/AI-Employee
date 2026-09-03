"""„Extra High" darf nicht still zu „High" werden.

Kundenmeldung 03.09.2026: Ein Betreiber stellte die Standard-Denktiefe auf
„Extra High" und bekam sie nicht. Die Kette bis zum Provider stimmte —
nachgemessen am laufenden Agenten: `default_reasoning='max'` →
`llm_default_reasoning_effort()='xhigh'`. Erst der Provider stufte herab, und
zwar nach dem NAMEN des Modells: „xhigh nur fuer die Codex-Familie".

Am echten Endpunkt des Kunden gemessen: sein Modell nimmt `xhigh` an (HTTP 200).
Die Namensregel war zum Zeitpunkt des Schreibens richtig und mit jedem neuen
Modell falscher — und weil das Herabstufen still geschah, fiel es niemandem auf.

Jetzt wird die gewuenschte Stufe gesendet und nur bei echter Ablehnung einmalig
herabgesetzt; dasselbe Muster benutzt die Datei schon fuer `temperature` und den
Namen des Token-Feldes.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import openai_provider as op  # noqa: E402


def _provider(model: str, effort: str = "xhigh"):
    p = op.OpenAIProvider.__new__(op.OpenAIProvider)
    p.model_name = model
    p.reasoning_effort = effort
    return p


class KeinHerabstufenNachNamenTests(unittest.TestCase):
    def setUp(self):
        op._XHIGH_ABGELEHNT.clear()

    def tearDown(self):
        op._XHIGH_ABGELEHNT.clear()

    def test_das_modell_des_kunden_bekommt_xhigh(self):
        """Der gemeldete Fall. Vorher kam hier „high" heraus."""
        self.assertEqual(_provider("gpt-5.6-luna")._effective_reasoning_effort(), "xhigh")

    def test_auch_ein_beliebiges_neues_modell(self):
        """Der eigentliche Fehler war die Annahme, man kenne alle Modelle."""
        for m in ("gpt-6", "o5-pro", "irgendwas-neues-2027"):
            self.assertEqual(_provider(m)._effective_reasoning_effort(), "xhigh", m)

    def test_codex_natuerlich_weiterhin(self):
        self.assertEqual(_provider("gpt-5.1-codex")._effective_reasoning_effort(), "xhigh")

    def test_erst_eine_echte_ablehnung_stuft_herab(self):
        op._XHIGH_ABGELEHNT.add("gpt-5.6-luna")
        self.assertEqual(_provider("gpt-5.6-luna")._effective_reasoning_effort(), "high")

    def test_die_ablehnung_gilt_nur_fuer_dieses_modell(self):
        op._XHIGH_ABGELEHNT.add("altes-modell")
        self.assertEqual(_provider("gpt-5.6-luna")._effective_reasoning_effort(), "xhigh")

    def test_andere_stufen_bleiben_unangetastet(self):
        for stufe in ("low", "medium", "high"):
            self.assertEqual(_provider("gpt-5.6-luna", stufe)._effective_reasoning_effort(), stufe)


class DieAblehnungWirdEngErkanntTests(unittest.TestCase):
    """Ein beliebiger Fehler darf die Einstellung des Betreibers nicht
    dauerhaft herabsetzen — das waere derselbe stille Verlust in neuem Gewand."""

    def test_eine_meldung_ueber_xhigh(self):
        self.assertTrue(op._lehnt_xhigh_ab(
            '{"error":{"message":"Invalid value for reasoning.effort: xhigh"}}'))

    def test_eine_meldung_ueber_die_stufe_allgemein(self):
        self.assertTrue(op._lehnt_xhigh_ab(
            "reasoning.effort: unsupported value for this model"))
        self.assertTrue(op._lehnt_xhigh_ab("effort does not support this model"))

    def test_ein_zu_langer_verlauf_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_xhigh_ab(
            '{"error":{"message":"maximum context length exceeded"}}'))

    def test_ein_ratenlimit_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_xhigh_ab("Rate limit reached for requests"))

    def test_ein_temperaturfehler_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_xhigh_ab(
            "temperature does not support 0.7 with this model"))

    def test_leer_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_xhigh_ab(""))
        self.assertFalse(op._lehnt_xhigh_ab(None))


class DerRueckfallIstVerdrahtetTests(unittest.TestCase):
    QUELLE = (Path(__file__).resolve().parents[1] / "app" / "providers"
              / "openai_provider.py").read_text()

    def test_im_responses_pfad(self):
        block = self.QUELLE.split("async def _stream_responses(", 1)[1][:3000]
        self.assertIn("_XHIGH_ABGELEHNT.add(self.model_name)", block)
        self.assertIn("_lehnt_xhigh_ab(error_text)", block)

    def test_im_chat_pfad(self):
        block = self.QUELLE.split("if body.get(\"reasoning_effort\") == \"xhigh\"", 1)[1][:600]
        self.assertIn("_XHIGH_ABGELEHNT.add(self.model_name)", block)
        self.assertIn('retry_body["reasoning_effort"] = "high"', block)

    def test_nur_bei_400(self):
        """Ein 500 oder ein Netzfehler sagt nichts ueber die Stufe aus."""
        block = self.QUELLE.split("async def _stream_responses(", 1)[1][:3000]
        self.assertIn("response.status_code == 400", block)

    def test_der_rueckfall_baut_den_koerper_nicht_von_hand(self):
        """Von Hand umgebaut wuerde er beim naechsten Feld wieder abweichen."""
        block = self.QUELLE.split("_XHIGH_ABGELEHNT.add(self.model_name)", 1)[1][:800]
        self.assertIn("self._stream_responses(url, messages, tools)", block)

    def test_es_wird_protokolliert(self):
        """Stilles Herabstufen war der Kern des Fehlers."""
        self.assertIn("nimmt kein xhigh", self.QUELLE)


if __name__ == "__main__":
    unittest.main()
