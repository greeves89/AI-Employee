"""Telegram: EINE Nachricht, die mitwächst — statt einer Kette fertiger Blöcke.

Kundenmeldung 2026-08-06: „meine pumpt einfach nur die nachricht raus" — man
wartet lange und bekommt dann alles am Stück. Andere Bots bearbeiten stattdessen
eine gesendete Nachricht laufend, sodass die Antwort sichtbar entsteht.

Die Textstücke lagen längst an: `chat_handler` veröffentlicht Deltas
(`current_full_text[seen_text_len:]`). Der Bot sammelte sie nur drei Sekunden
und schickte dann eine NEUE Nachricht.

Sicherheitsregel dabei: Der DLP-Egress-Filter prüft den FERTIGEN Text. Ein
Live-Bild würde ihn umgehen — ein Secret wäre sichtbar, bevor der Filter greift.
Deshalb: bei aktivem DLP keine Zwischenstände.
"""

import pathlib
import re
import unittest

BOT = pathlib.Path(__file__).resolve().parents[1] / "app/telegram/agent_bot.py"


def _method(src: str, name: str) -> str:
    """Quelltext EINER Methode. Ohne Import, weil python-telegram-bot in der
    Testumgebung nicht installiert ist."""
    m = re.search(rf"\n    async def {name}\(.*?(?=\n    async def |\n    def |\nclass )", src, re.S)
    return m.group(0) if m else ""


class LiveMessageTests(unittest.TestCase):
    def setUp(self):
        self.src = BOT.read_text()

    def test_live_update_exists(self):
        self.assertIn("async def _live_update", self.src)

    def test_text_chunks_update_live(self):
        """Jedes Textstück zieht die Nachricht nach — das ist der Kern."""
        self.assertIn("await self._live_update(chat_id, full_response)", self.src)

    def test_tool_calls_show_a_working_line(self):
        """Statt einer weiteren Nachricht: eine kursive Zeile unter dem Text."""
        self.assertIn("nutzt gerade", self.src)
        self.assertIn("status=live_status", self.src)

    def test_final_flush_goes_through_dlp(self):
        """Der fertige Text MUSS geprüft werden — sonst wäre der Filter wirkungslos."""
        block = _method(self.src, "_live_update")
        self.assertIn("_dlp_text", block)
        self.assertIn("final", block)
        self.assertIn("await self._live_update(chat_id, full_response, final=True)", self.src)

    def test_no_live_preview_while_dlp_is_active(self):
        """Die eigentliche Sicherheitsregel: kein Zwischenstand am Filter vorbei."""
        self.assertIn("_dlp_active", _method(self.src, "_live_update"))

    def test_dlp_check_fails_closed(self):
        """Lässt sich der Filterstatus nicht lesen, wird NICHT live gezeigt."""
        self.assertIn("return True", _method(self.src, "_dlp_active"))

    def test_edits_are_rate_limited(self):
        """Telegram drosselt häufige Bearbeitungen — sonst 429 und nichts kommt an."""
        self.assertIn("1.3", _method(self.src, "_live_update"))

    def test_long_text_falls_back_to_chunking(self):
        """Über 4096 Zeichen kann Telegram nicht bearbeiten — dann der alte Weg."""
        block = _method(self.src, "_live_update")
        self.assertIn("_send_chunked", block)
        self.assertIn("4000", block)


if __name__ == "__main__":
    unittest.main()



class ToolLabelTests(unittest.TestCase):
    """Werkzeugnamen lesbar: im Chat stand „mcp__orchestrator__create_task"."""

    def test_mcp_names_are_humanised(self):
        import re as _re
        src = BOT.read_text()
        m = _re.search(r'def _tool_label.*?return name\.replace\("_", " "\)', src, _re.S)
        ns: dict = {}
        exec(m.group(0), ns)
        f = ns["_tool_label"]
        self.assertEqual(f("mcp__orchestrator__create_task"), "Orchestrator: create task")
        self.assertEqual(f("Bash"), "Bash")

    def test_status_line_has_no_markdown(self):
        """Die Nachricht geht ohne parse_mode raus — Unterstriche stünden wörtlich da."""
        src = BOT.read_text()
        self.assertNotIn('f"\\n\\n_{status}_"', src)

    def test_logger_exists(self):
        """logging war importiert, ein logger aber nie angelegt — die Fehlerzeile
        im except-Block hätte einen NameError geworfen und den Turn zerlegt."""
        self.assertIn("logger = logging.getLogger(__name__)", BOT.read_text())


class HandlerRobustnessTests(unittest.TestCase):
    """Beiwerk darf die Zustellung niemals verhindern.

    Vorfall 2026-08-06: `self._last_user_msg[chat_id] = …` stand im Eingangs-Handler,
    das Feld wurde aber erst im Antwort-Lauscher angelegt — der läuft später. Beim
    ersten Mal warf es AttributeError, der Handler brach ab, und die Nachricht
    erreichte den Agenten nie. Der Nutzer bekam nur noch die Reaktion, keine Antwort.
    """

    def setUp(self):
        self.src = BOT.read_text()

    def test_state_is_created_in_the_constructor(self):
        """Nicht per hasattr im Lauscher — der Handler läuft vorher."""
        ctor = re.search(r"def __init__\(.*?(?=\n    async def |\n    def )", self.src, re.S)
        self.assertIsNotNone(ctor)
        self.assertIn("self._live: dict = {}", ctor.group(0))
        self.assertIn("self._last_user_msg: dict = {}", ctor.group(0))

    def test_no_late_hasattr_initialisation(self):
        """Die späte Notlösung darf nicht zurückkehren."""
        self.assertNotIn('hasattr(self, "_last_user_msg")', self.src)

    def test_reaction_block_cannot_break_delivery(self):
        """Reaktion und Buchführung stehen in einem eigenen try — sonst hängt die
        Zustellung an einer Nebensächlichkeit."""
        block = re.search(r"# Merken, welche Nachricht.*?_start_listener", self.src, re.S)
        self.assertIsNotNone(block)
        self.assertIn("try:", block.group(0))
        self.assertIn("except Exception", block.group(0))


class SpinnerAndDetailTests(unittest.TestCase):
    """Die Arbeitszeile soll zeigen, was WIRKLICH passiert — nicht nur „nutzt Bash".

    Kundenwunsch 2026-08-06: „bei bash dauert das aktuell sehr lang, dass der
    detaillierter reinschreibt was gerade passiert" — plus ein Spinner, damit
    sichtbar bleibt, dass etwas läuft.
    """

    def setUp(self):
        self.src = BOT.read_text()

    def test_detail_comes_from_the_tool_input(self):
        """Die Angaben lagen im Werkzeugaufruf längst vor."""
        m = re.search(r"def _tool_detail.*?return val\[:70\].*?\n", self.src, re.S)
        self.assertIsNotNone(m)
        ns: dict = {}
        exec(m.group(0), ns)
        f = ns["_tool_detail"]
        self.assertEqual(f("Bash", {"command": "python3 watcher.py"}), "python3 watcher.py")
        self.assertEqual(f("Read", {"file_path": "/workspace/projects/sap/main.py"}), "sap/main.py")
        self.assertEqual(f("mcp__memory__memory_search", {"query": "SAP MDG"}), "SAP MDG")

    def test_missing_detail_is_not_faked(self):
        """Ohne Angabe bleibt die Zeile schlicht — nichts erfinden."""
        m = re.search(r"def _tool_detail.*?return val\[:70\].*?\n", self.src, re.S)
        ns: dict = {}
        exec(m.group(0), ns)
        self.assertEqual(ns["_tool_detail"]("Bash", {}), "")
        self.assertEqual(ns["_tool_detail"]("Bash", None), "")

    def test_long_detail_is_shortened(self):
        m = re.search(r"def _tool_detail.*?return val\[:70\].*?\n", self.src, re.S)
        ns: dict = {}
        exec(m.group(0), ns)
        out = ns["_tool_detail"]("Bash", {"command": "x" * 200})
        self.assertLessEqual(len(out), 71)
        self.assertTrue(out.endswith("…"))

    def test_spinner_advances_per_edit(self):
        self.assertIn("SPINNER", self.src)
        self.assertIn('state["tick"]', self.src)

    def test_spinner_keeps_turning_without_events(self):
        """Bei einem langen Bash kommt nichts herein — ohne Takt stünde er still."""
        self.assertIn("Spinner weiterdrehen", self.src)
