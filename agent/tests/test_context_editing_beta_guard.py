"""Eine abgelehnte Beta darf nicht den ganzen Chat-Weg mitreissen.

Context-Editing (#538) laesst Anthropic serverseitig alte Werkzeug-Ausgaben
wegraeumen. Es ist eine BETA: laeuft sie aus oder kennt ein Modell sie nicht,
antwortet die API mit 400. Wurde der Parameter bedingungslos mitgeschickt, waere
damit JEDE Anfrage ueber diesen Provider tot — wegen einer Bequemlichkeit, die
nur den Verlauf kleiner haelt und fuer die Funktion selbst entbehrlich ist.

Nach der ersten Ablehnung wird sie deshalb dauerhaft weggelassen.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import anthropic_provider as ap  # noqa: E402


class DieAblehnungWirdErkanntTests(unittest.TestCase):
    def test_eine_meldung_ueber_context_management_zaehlt(self):
        self.assertTrue(ap._betrifft_context_editing(
            '{"error":{"message":"context_management: unsupported"}}'))

    def test_auch_die_beta_kennung_zaehlt(self):
        self.assertTrue(ap._betrifft_context_editing(
            "unsupported anthropic-beta: context-management-2025-06-27"))

    def test_und_der_strategiename(self):
        self.assertTrue(ap._betrifft_context_editing("clear_tool_uses_20250919 not allowed"))

    def test_ein_gewoehnlicher_eingabefehler_zaehlt_nicht(self):
        """Zu breit gefasst wuerde die Beta bei jedem Tippfehler abschalten."""
        self.assertFalse(ap._betrifft_context_editing(
            '{"error":{"message":"max_tokens: must be greater than 0"}}'))
        self.assertFalse(ap._betrifft_context_editing("model not found"))
        self.assertFalse(ap._betrifft_context_editing(""))
        self.assertFalse(ap._betrifft_context_editing(None))


class DerParameterHaengtAmMerkzeichenTests(unittest.TestCase):
    """Geprueft wird die Quelle: den Stream ohne echten Endpunkt zu fahren,
    hiesse die halbe Bibliothek nachzubauen."""

    QUELLE = (Path(__file__).resolve().parents[1]
              / "app" / "providers" / "anthropic_provider.py").read_text()

    def test_er_wird_nur_gesetzt_solange_nichts_abgelehnt_wurde(self):
        self.assertIn("if not _CONTEXT_EDITING_AUS:", self.QUELLE)
        block = self.QUELLE.split("if not _CONTEXT_EDITING_AUS:", 1)[1][:400]
        self.assertIn('body["context_management"]', block)
        self.assertIn('headers["anthropic-beta"]', block)

    def test_beides_haengt_zusammen(self):
        """Der Beta-Kopf ohne den Parameter waere sinnlos, der Parameter ohne
        den Kopf ein garantierter 400."""
        # Nur die ZUWEISUNG zaehlen — der gleichnamige Eintrag in der
        # Erkennungsliste ist kein zweiter Setzort.
        vorkommen = self.QUELLE.count('headers["anthropic-beta"] =')
        self.assertEqual(vorkommen, 1, "Der Beta-Kopf darf nur an EINER Stelle gesetzt werden")
        self.assertNotIn('"anthropic-beta": "context-management', self.QUELLE,
                         "Der Kopf darf nicht zusaetzlich unbedingt im headers-Wörterbuch stehen")

    def test_die_ablehnung_schaltet_dauerhaft_ab(self):
        block = self.QUELLE.split("_betrifft_context_editing(text)", 1)[1][:600]
        self.assertIn("_CONTEXT_EDITING_AUS = True", block)

    def test_das_merkzeichen_gilt_ueber_instanzen_hinweg(self):
        """Je Instanz gemerkt, liefe der naechste Provider erneut hinein."""
        self.assertIn("global _CONTEXT_EDITING_AUS", self.QUELLE)
        self.assertTrue(hasattr(ap, "_CONTEXT_EDITING_AUS"))

    def test_der_nutzer_erfaehrt_was_zu_tun_ist(self):
        """Ein roher API-Fehler waere hier eine Sackgasse."""
        self.assertIn("schick die Nachricht einfach nochmal", self.QUELLE)

    def test_der_grund_wird_im_wortlaut_protokolliert(self):
        self.assertIn("Grund im Wortlaut", self.QUELLE)


if __name__ == "__main__":
    unittest.main()
