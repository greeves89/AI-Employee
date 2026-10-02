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

    def _folge(self, *nachrichten):
        from app.chat_handler import TextBloecke
        bloecke = TextBloecke()
        return [s for s in (bloecke.neu(m) for m in nachrichten) if s]

    def test_ankuendigung_werkzeug_antwort(self):
        """02.10.2026: „…was wir wissen.Drei kurze Ideen …" — zwischen Ankündigung
        und Antwort lag ein Werkzeugaufruf ohne Text, an dem die Markierung verfiel."""
        stuecke = self._folge(
            {"id": "m1", "content": [{"type": "text", "text": "Ich schau kurz nach."}]},
            {"id": "m1", "content": [{"type": "tool_use", "id": "t1", "name": "brain_search"}]},
            {"id": "m2", "content": [{"type": "text", "text": "Drei kurze Ideen"}]},
        )
        self.assertEqual(stuecke, [("Ich schau kurz nach.", False), ("Drei kurze Ideen", True)])

    def test_laengere_neue_nachricht_verliert_keinen_anfang(self):
        # Ohne Nachrichten-ID-Vergleich galt nur „Text wurde kürzer" als Zugwechsel;
        # eine längere zweite Nachricht verlor dann ihre ersten Zeichen.
        stuecke = self._folge(
            {"id": "m1", "content": [{"type": "text", "text": "Kurz."}]},
            {"id": "m2", "content": [{"type": "text", "text": "Eine deutlich längere Antwort"}]},
        )
        self.assertEqual(stuecke[1], ("Eine deutlich längere Antwort", True))

    def test_wachsender_text_derselben_nachricht_ist_fortsetzung(self):
        stuecke = self._folge(
            {"id": "m1", "content": [{"type": "text", "text": "Hallo"}]},
            {"id": "m1", "content": [{"type": "text", "text": "Hallo Welt"}]},
        )
        self.assertEqual(stuecke, [("Hallo", False), (" Welt", False)])

    def test_ohne_id_zaehlt_das_kuerzerwerden(self):
        stuecke = self._folge(
            {"content": [{"type": "text", "text": "Erster Zug"}]},
            {"content": [{"type": "tool_use"}]},
            {"content": [{"type": "text", "text": "Zweiter"}]},
        )
        self.assertEqual(stuecke, [("Erster Zug", False), ("Zweiter", True)])

    def test_chat_handler_nutzt_die_klasse(self):
        quelle = (_AGENT / "chat_handler.py").read_text(encoding="utf-8")
        self.assertIn("bloecke.neu(message)", quelle)

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


class GespeicherterVerlaufKlebtNicht(unittest.IsolatedAsyncioTestCase):
    """27.09.2026: Live war die Luecke da, nach dem Neuladen nicht — gespeichert
    wurde ``"".join(...)``. Der Text oben prueft nur, dass die Markierung im
    Quelltext steht; das hier faehrt den echten Codex-Lauf."""

    async def test_codex_ergebnis_trennt_aeusserungen(self):
        import json
        import os
        import tempfile
        from unittest.mock import AsyncMock, MagicMock, patch

        from app.codex_runner import CodexAgentRunner
        from tests.test_codex_exit_preserves_partial_result import _FakeProcess

        def ereignis(text):
            return (json.dumps({"type": "item.completed",
                                "item": {"type": "agent_message", "text": text}}) + "\n").encode()

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        publisher.publish_chat = AsyncMock()
        publisher.last_activity_at = 0.0
        runner = CodexAgentRunner(publisher)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"CODEX_HOME": tmp}), \
                patch("app.codex_runner._codex_auth_problem", return_value=None), \
                patch("app.codex_runner.codex_auth_sync.push_if_rotated", AsyncMock(return_value=False)), \
                patch("app.codex_runner.asyncio.create_subprocess_exec", AsyncMock(return_value=_FakeProcess(
                    [ereignis("Ich lege den Job an."), ereignis("Erledigt!")], [], 0))):
            ergebnis = await runner._run_codex("t1", "prompt", "model", stream="task")

        # Der Verlauf trennt die Aeusserungen als Absatz ...
        self.assertEqual(ergebnis.get("text"), "Ich lege den Job an.\n\nErledigt!")
        # ... das Aufgaben-Ergebnis ist nur die Schlussantwort (wie bei Claude Code).
        self.assertEqual(ergebnis.get("result"), "Erledigt!")

