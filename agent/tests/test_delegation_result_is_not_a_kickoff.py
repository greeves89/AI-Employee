"""Ein zurückgekommenes Ergebnis ist kein „wurde angestoßen".

Kundenfall vom 2026-08-13. Die ganze Kette war korrekt — nachweisbar aus den
Werkzeugaufrufen des Team-Leads:

    list_my_team  →  bash + read_file (Projekt geprüft)  →  update_todos
    →  delegate_and_wait(agent_id="e9842ce8", timeout_seconds=300)
    →  memory_save, rate_task

Er kannte sein Team, hat das richtige Werkzeug mit dem richtigen Empfänger
benutzt, der Auftrag kam an und war nach 34 Sekunden fertig. Dass die Aufrufe
**nach** ``delegate_and_wait`` noch kamen, beweist: der Aufruf ist mit dem
Ergebnis zurückgekehrt, sonst hätte der Zug dort 300 Sekunden gehangen.

Und dann schrieb er dem Menschen:

    „Angestoßen: Mr. Design erstellt **jetzt** das Redesign-Paket."

Der Mensch las „läuft", wartete 18 Minuten und musste nachfragen — dabei war die
Arbeit beim Zurückkommen des Aufrufs längst erledigt.

Das ist kein Werkzeugfehler, sondern eine zu leise Rückgabe. ``1/1 Auftraege
abgeschlossen`` genügt nicht: der Unterschied zwischen „ich habe angestoßen" und
„ich habe das Ergebnis" muss in der Rückgabe selbst stehen — nicht in einer
Werkzeugbeschreibung, die zwanzig Züge vorher gelesen wurde.
"""

import asyncio
import unittest
from pathlib import Path

from app.tools.api_client import OrchestratorAPIClient

ROOT = Path(__file__).resolve().parents[2]


class _Client(OrchestratorAPIClient):
    def __init__(self, statuses):
        self.agent_id = "lead"
        self.agent_name = "Lead"
        self._statuses = statuses

    async def _request(self, method, path, json=None, params=None):  # noqa: A002
        if path.endswith("/batch"):
            return {"tasks": [{"id": tid} for tid in self._statuses]}
        tid = path.rsplit("/", 1)[-1]
        state = self._statuses.get(tid)
        if state is None:
            return "nicht abrufbar"
        return {"id": tid, "title": f"Auftrag {tid}", "status": state,
                "result": f"Ergebnis von {tid}"}


def _run(statuses, timeout=10):
    client = _Client(statuses)
    return asyncio.run(client.delegate_and_wait({
        "tasks": [{"title": t, "prompt": "x", "agent_id": "w"} for t in statuses],
        "timeout_seconds": timeout,
    }))


class EverythingDoneTests(unittest.TestCase):
    def setUp(self):
        self.out = _run({"t1": "completed"})

    def test_it_says_finished_first(self):
        self.assertTrue(self.out.startswith("FERTIG:"),
                        "Die erste Zeile entscheidet, wie das Modell den Rest liest")

    def test_it_names_the_result_as_final(self):
        self.assertIn("ENDERGEBNIS", self.out)

    def test_it_forbids_the_wording_that_caused_the_confusion(self):
        self.assertIn("angestossen", self.out.lower())
        self.assertIn("NICHT", self.out)

    def test_the_actual_result_is_still_there(self):
        self.assertIn("Ergebnis von t1", self.out)


class PartiallyDoneTests(unittest.TestCase):
    """Halb fertig darf nicht wie ganz fertig klingen — das war der Fehler in
    die andere Richtung, vom 2026-08-12."""

    def setUp(self):
        self.out = _run({"t1": "completed", "t2": "running"}, timeout=10)

    def test_it_says_partially(self):
        self.assertTrue(self.out.startswith("TEILWEISE FERTIG:"))

    def test_it_counts_both_sides(self):
        self.assertIn("1 von 2", self.out)

    def test_the_unfinished_one_is_named_as_unfinished(self):
        self.assertIn("laeuft noch", self.out)

    def test_a_failed_task_still_counts_as_returned(self):
        out = _run({"t1": "failed"})
        self.assertTrue(out.startswith("FERTIG:"))
        self.assertIn("failed", out)


class BothRuntimesSayItTests(unittest.TestCase):
    """Custom-LLM UND stdio-MCP (Claude Code, Codex). Eine Laufzeit ohne den
    Hinweis waere genau die Luecke, die in diesem Projekt schon mehrfach
    zugeschlagen hat."""

    def test_the_mcp_server_says_it_too(self):
        src = (ROOT / "agent/mcp/orchestrator-server.mjs").read_text()
        block = src.split('case "delegate_and_wait"')[1][:4000]
        self.assertIn("FERTIG: alle", block)
        self.assertIn("ENDERGEBNIS", block)

    def test_no_emoji_in_any_mcp_server(self):
        """Nutzersichtbarer Text — harte Vorgabe des Projekts.

        Gesucht wird im GANZEN Verzeichnis, nicht nur an der Stelle, um die es
        hier geht: die erste Fassung dieses Tests sah nur ein Fenster um
        ``delegate_and_wait`` und fand dabei zufaellig weitere Emojis in
        ``get_tasks_status``, im Brain- und im Freigabe-Server. Eine Regel, die
        nur dort geprueft wird, wo man gerade hinsieht, ist keine Regel.
        """
        for path in sorted((ROOT / "agent/mcp").glob("*.mjs")):
            text = path.read_text()
            for symbol in ("✅", "❌", "⏳", "🔄"):
                with self.subTest(datei=path.name, zeichen=symbol):
                    self.assertNotIn(symbol, text)


if __name__ == "__main__":
    unittest.main()
