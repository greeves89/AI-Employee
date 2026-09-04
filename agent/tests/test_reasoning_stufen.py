"""Die Denkstufen und ihre Uebersetzung je Harness.

"xhigh" und "max" sind ZWEI Stufen, nicht eine. Bis 1.312.x hiess die oberste
Stufe der Oberflaeche "max" und MEINTE das, was die Anbieter "xhigh" nennen —
oberhalb davon kennt die GPT-5.6-Familie aber noch ein echtes "max" (am
Endpunkt geprueft: alle drei Modelle des Betreibers nehmen es mit HTTP 200 an).

Die Zuordnung ist harness-abhaengig und lag frueher als dict-Literal an drei
Stellen verstreut. Jetzt zentral — dieser Test wacht darueber, dass sie es
bleibt und dass die Eigenheiten je Harness stimmen.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import CLAUDE_THINKING_BUDGET, reasoning_fuer

AGENT = Path(__file__).resolve().parents[1] / "app"


class LlmWegTests(unittest.TestCase):
    def test_max_geht_als_max_raus(self):
        """Der Kern: Frueher wurde daraus xhigh, die Stufe war nicht erreichbar."""
        self.assertEqual("max", reasoning_fuer("llm", "max"))

    def test_xhigh_bleibt_xhigh(self):
        self.assertEqual("xhigh", reasoning_fuer("llm", "xhigh"))

    def test_aus_ist_leer(self):
        """Leer heisst: nichts erzwingen, der Anbieter entscheidet."""
        self.assertEqual("", reasoning_fuer("llm", "off"))

    def test_uebrige_stufen_unveraendert(self):
        for s in ("low", "medium", "high"):
            with self.subTest(stufe=s):
                self.assertEqual(s, reasoning_fuer("llm", s))

    def test_leere_eingabe_bleibt_leer(self):
        self.assertEqual("", reasoning_fuer("llm", ""))
        self.assertEqual("", reasoning_fuer("llm", None))

    def test_gross_und_leerzeichen_stoeren_nicht(self):
        self.assertEqual("max", reasoning_fuer("llm", "  MAX "))


class CodexWegTests(unittest.TestCase):
    def test_codex_kennt_kein_max(self):
        """Codex hoert bei xhigh auf — sonst liefe der Lauf in einen Fehler."""
        self.assertEqual("xhigh", reasoning_fuer("codex", "max"))

    def test_codex_nennt_aus_minimal(self):
        self.assertEqual("minimal", reasoning_fuer("codex", "off"))

    def test_codex_xhigh_bleibt(self):
        self.assertEqual("xhigh", reasoning_fuer("codex", "xhigh"))


class ClaudeWegTests(unittest.TestCase):
    def test_beide_oberen_stufen_haben_ein_budget(self):
        """Fehlt eine, faellt sie stumm auf den Container-Standard zurueck."""
        for s in ("xhigh", "max"):
            with self.subTest(stufe=s):
                self.assertIn(s, CLAUDE_THINKING_BUDGET)

    def test_claude_hat_oberhalb_von_ultrathink_nichts_mehr(self):
        self.assertEqual(CLAUDE_THINKING_BUDGET["xhigh"], CLAUDE_THINKING_BUDGET["max"])


class KeineVerstreutenTabellenTests(unittest.TestCase):
    """Die Zuordnung stand an drei Stellen — beim Ergaenzen einer Stufe haette
    man leicht eine uebersehen. Genau das ist passiert."""

    def test_die_aufrufer_bauen_sie_nicht_selbst(self):
        for datei in ("codex_runner.py", "llm_chat_handler.py"):
            quelle = (AGENT / datei).read_text()
            with self.subTest(datei=datei):
                self.assertIn("reasoning_fuer", quelle)
                self.assertNotIn('{"off": "", "max": "xhigh"}', quelle)
                self.assertNotIn('{"off": "minimal", "max": "xhigh"}', quelle)


if __name__ == "__main__":
    unittest.main()
