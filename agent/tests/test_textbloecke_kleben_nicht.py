"""Mehrere Zwischenmeldungen duerfen im Chat nicht zu einem Fliesstext verkleben.

Am 22.09.2026 stand in der Oberflaeche:

    "Beide laufen jetzt. Ich check in Intervallen.Beide noch in der
     Warteschlange, keine Fehler. Ich bleib dran.Beide noch am Warten ..."

Kein Leerzeichen, kein Absatz — vier eigenstaendige Statusmeldungen aus vier
Zuegen, zu einem Block verkettet.

Ursache: Das Backend schickt zweierlei durch denselben Kanal.

* Innerhalb EINER Antwort kommen echte Teilstuecke (Delta). Die gehoeren
  aneinander, Verketten ist richtig.
* Beginnt ein NEUER Zug, wird der Zaehler zurueckgesetzt und die vollstaendige
  neue Aeusserung geschickt. Die gehoert NICHT an die vorige.

Beides sah gleich aus, also klebte die Oberflaeche alles zusammen. Jetzt
markiert der Absender die Grenze (``neuer_block``), und nur er kann das
wissen — die Oberflaeche kann es nicht erraten.

Bei Codex ist jedes Textstueck ohnehin eine fertige Aeusserung; dort ist die
Markierung deshalb immer gesetzt.
"""

import re
import unittest
from pathlib import Path

_AGENT = Path(__file__).resolve().parents[1] / "app"
_FRONTEND = (Path(__file__).resolve().parents[2]
             / "frontend" / "src" / "components" / "agents" / "chat.tsx")


class AbsenderMarkiertDieGrenze(unittest.TestCase):

    def test_claude_code_markiert_den_zugwechsel(self):
        quelle = (_AGENT / "chat_handler.py").read_text(encoding="utf-8")
        self.assertIn("neuer_block", quelle,
                      "Ohne Markierung kann die Oberflaeche Fortsetzung und "
                      "neue Aeusserung nicht unterscheiden.")
        # Die Markierung muss am ZURUECKSETZEN des Zaehlers haengen — genau dort
        # beginnt ein neuer Zug. (Nicht an der Initialisierung weiter oben.)
        treffer = re.search(
            r"if len\(current_full_text\) < seen_text_len:(.{0,200}?)\n\n",
            quelle, re.S)
        self.assertIsNotNone(treffer, "Der Zugwechsel-Zweig wurde nicht gefunden.")
        self.assertIn(
            "neuer_block = True", treffer.group(1),
            "Die Markierung haengt nicht am Zugwechsel — dann trennt sie die "
            "falschen Stellen.",
        )

    def test_codex_schickt_jedes_stueck_als_eigenen_block(self):
        quelle = (_AGENT / "codex_runner.py").read_text(encoding="utf-8")
        treffer = re.search(r'"text",\s*\n?\s*\{"text": text[^}]*\}', quelle)
        self.assertIsNotNone(treffer, "Text-Veroeffentlichung nicht gefunden")
        self.assertIn("neuer_block", treffer.group(0))


@unittest.skipUnless(_FRONTEND.exists(), "Frontend nicht vorhanden")
class OberflaecheBeachtetDieGrenze(unittest.TestCase):

    def setUp(self):
        self.quelle = _FRONTEND.read_text(encoding="utf-8")

    def test_bei_neuem_block_wird_nicht_angehaengt(self):
        self.assertRegex(
            self.quelle,
            r'lastStep\.type === "text" && !neuerBlock',
            "Die Oberflaeche haengt weiterhin bedingungslos an — genau daraus "
            "entstand der Fliesstext ohne Luecke.",
        )

    def test_es_gibt_einen_sichtbaren_wartehinweis(self):
        """Solange Text dasteht, sah ein laufender Zug aus wie eine fertige
        Antwort. Wer wartete, wusste nicht, worauf."""
        self.assertIn("ArbeitetWeiter", self.quelle)
        self.assertRegex(
            self.quelle,
            r"message\.isStreaming && !noVisibleContent",
            "Der Hinweis darf nicht nur erscheinen, solange NOCH NICHTS dasteht.",
        )


if __name__ == "__main__":
    unittest.main()
