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
        op._STUFE_ABGELEHNT.clear()

    def tearDown(self):
        op._STUFE_ABGELEHNT.clear()

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
        op._STUFE_ABGELEHNT.add(("gpt-5.6-luna", "xhigh"))
        self.assertEqual(_provider("gpt-5.6-luna")._effective_reasoning_effort(), "high")

    def test_die_ablehnung_gilt_nur_fuer_dieses_modell(self):
        op._STUFE_ABGELEHNT.add(("altes-modell", "xhigh"))
        self.assertEqual(_provider("gpt-5.6-luna")._effective_reasoning_effort(), "xhigh")

    def test_andere_stufen_bleiben_unangetastet(self):
        for stufe in ("low", "medium", "high"):
            self.assertEqual(_provider("gpt-5.6-luna", stufe)._effective_reasoning_effort(), stufe)


class DieAblehnungWirdEngErkanntTests(unittest.TestCase):
    """Ein beliebiger Fehler darf die Einstellung des Betreibers nicht
    dauerhaft herabsetzen — das waere derselbe stille Verlust in neuem Gewand."""

    def test_eine_meldung_ueber_die_genannte_stufe(self):
        self.assertTrue(op._lehnt_stufe_ab(
            '{"error":{"message":"Invalid value for reasoning.effort: xhigh"}}',
            "xhigh"))
        self.assertTrue(op._lehnt_stufe_ab(
            '{"error":{"message":"Invalid value for reasoning.effort: max"}}',
            "max"))

    def test_eine_meldung_ueber_die_stufe_allgemein(self):
        self.assertTrue(op._lehnt_stufe_ab(
            "reasoning.effort: unsupported value for this model", "xhigh"))
        self.assertTrue(op._lehnt_stufe_ab(
            "effort does not support this model", "max"))

    def test_ein_zu_langer_verlauf_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_stufe_ab(
            '{"error":{"message":"maximum context length exceeded"}}', "xhigh"))

    def test_ein_ratenlimit_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_stufe_ab("Rate limit reached for requests", "xhigh"))

    def test_ein_temperaturfehler_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_stufe_ab(
            "temperature does not support 0.7 with this model", "xhigh"))

    def test_leer_zaehlt_nicht(self):
        self.assertFalse(op._lehnt_stufe_ab("", "xhigh"))
        self.assertFalse(op._lehnt_stufe_ab(None, "xhigh"))


class DieKetteGehtNurEineStufeTiefTests(unittest.TestCase):
    """Wer "max" gewaehlt hat, will bei einem Modell ohne "max" das
    naechstbeste — nicht zwei Stufen weniger."""

    def setUp(self):
        op._STUFE_ABGELEHNT.clear()

    def tearDown(self):
        op._STUFE_ABGELEHNT.clear()

    def test_max_faellt_auf_xhigh(self):
        op._STUFE_ABGELEHNT.add(("modell-x", "max"))
        self.assertEqual("xhigh", _provider("modell-x", "max")._effective_reasoning_effort())

    def test_kennt_es_auch_xhigh_nicht_geht_es_weiter_auf_high(self):
        op._STUFE_ABGELEHNT.add(("modell-x", "max"))
        op._STUFE_ABGELEHNT.add(("modell-x", "xhigh"))
        self.assertEqual("high", _provider("modell-x", "max")._effective_reasoning_effort())

    def test_die_ablehnung_von_max_sagt_nichts_ueber_xhigh(self):
        """Sonst verloere ein Modell, das xhigh kann, diese Stufe gleich mit."""
        op._STUFE_ABGELEHNT.add(("modell-x", "max"))
        self.assertEqual("xhigh", _provider("modell-x", "xhigh")._effective_reasoning_effort())

    def test_die_kette_endet(self):
        """Auch wenn alles abgelehnt ist, darf sie nicht endlos laufen."""
        for s in ("max", "xhigh", "high"):
            op._STUFE_ABGELEHNT.add(("modell-x", s))
        self.assertEqual("high", _provider("modell-x", "max")._effective_reasoning_effort())


class DerRueckfallIstVerdrahtetTests(unittest.TestCase):
    QUELLE = (Path(__file__).resolve().parents[1] / "app" / "providers"
              / "openai_provider.py").read_text()

    def test_im_responses_pfad(self):
        block = self.QUELLE.split("async def _stream_responses(", 1)[1][:3000]
        self.assertIn("_STUFE_ABGELEHNT.add((self.model_name, _gesendete_stufe))", block)
        self.assertIn("_lehnt_stufe_ab(error_text, _gesendete_stufe)", block)

    def test_im_chat_pfad(self):
        block = self.QUELLE.split("_stufe = body.get(\"reasoning_effort\")", 1)[1][:800]
        self.assertIn("_STUFE_ABGELEHNT.add((self.model_name, _stufe))", block)
        # Eine Stufe tiefer, nicht pauschal auf "high".
        self.assertIn("_NAECHSTTIEFER[_stufe]", block)

    def test_nur_bei_400(self):
        """Ein 500 oder ein Netzfehler sagt nichts ueber die Stufe aus."""
        block = self.QUELLE.split("async def _stream_responses(", 1)[1][:3000]
        self.assertIn("response.status_code == 400", block)

    def test_der_rueckfall_baut_den_koerper_nicht_von_hand(self):
        """Von Hand umgebaut wuerde er beim naechsten Feld wieder abweichen."""
        block = self.QUELLE.split("_STUFE_ABGELEHNT.add((self.model_name, _gesendete_stufe))", 1)[1][:800]
        self.assertIn("self._stream_responses(url, messages, tools)", block)

    def test_es_wird_protokolliert(self):
        """Stilles Herabstufen war der Kern des Fehlers."""
        self.assertIn("nimmt kein %s", self.QUELLE)


if __name__ == "__main__":
    unittest.main()
