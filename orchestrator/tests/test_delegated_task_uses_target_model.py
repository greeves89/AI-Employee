"""Ein delegierter Auftrag laeuft unter dem Modell des ZIELAGENTEN.

Vorgabe des Nutzers am 19.08.2026: „wenn delegiert SOLL das eingestellte Modell
des Agent verwendet werden".

Vorher haengte jedes Delegier-Werkzeug ``model: DEFAULT_MODEL`` an — das Modell
des AUFTRAGGEBERS. Zwei Folgen:

1. Ein Kollege arbeitete unter einem Modell, das er sich nie ausgesucht hat.
2. Der Model-Router des Zielagenten kam nie zum Zug: der Orchestrator fragt ihn
   ausdruecklich nur, wenn KEIN Modell mitkam (``if model is None``). Deshalb
   war der Router bei delegierten Auftraegen strukturell wirkungslos — unabhaengig
   davon, ob er eingeschaltet war.

Der Custom-LLM-Weg machte es ohnehin schon richtig (``if t.get("model")``); die
Luecke bestand nur im MCP-Weg, also bei Claude Code und Codex. Wieder eine
Paritaetsluecke zwischen den Laufzeiten.
"""

import unittest
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
MCP = (WURZEL / "agent/mcp/orchestrator-server.mjs").read_text()
#: Ohne Kommentarzeilen — der Hergang steht dort woertlich drin, samt der
#: Zeile, die es aus dem CODE zu verbannen gilt.
MCP_CODE = "\n".join(
    z for z in MCP.splitlines() if not z.lstrip().startswith(("//", "*", "/*"))
)
CUSTOM = (WURZEL / "agent/app/tools/api_client.py").read_text()
ROUTER = (WURZEL / "orchestrator/app/core/task_router.py").read_text()


class NoDelegationCarriesTheCallersModelTests(unittest.TestCase):
    def test_no_tool_attaches_the_default_model_any_more(self):
        """Das war der Fehler — an allen vier Stellen."""
        self.assertNotIn("model: DEFAULT_MODEL", MCP_CODE)

    def test_the_creating_tools_send_no_model_at_all(self):
        """Kein Modell im Auftrag heisst: der Zielagent nimmt seines."""
        for werkzeug in ('case "create_task":', 'case "create_task_batch":',
                         'case "delegate_and_wait":'):
            with self.subTest(werkzeug=werkzeug):
                block = MCP.split(werkzeug, 1)[1][:700]
                self.assertNotIn("model:", block)

    def test_an_explicitly_requested_model_still_gets_through(self):
        """Der Trigger-Weg laesst den Nutzer ausdruecklich eines waehlen — das
        darf die Aenderung nicht mitnehmen."""
        self.assertIn("model: args.model || null", MCP)

    def test_the_custom_llm_path_was_already_correct(self):
        """Nur mitschicken, wenn wirklich eines gesetzt ist."""
        self.assertIn('**({"model": t["model"]} if t.get("model") else {})', CUSTOM)


class TheOrchestratorFallsBackToTheTargetTests(unittest.TestCase):
    def test_no_model_means_the_agent_uses_its_own(self):
        """Der Vertrag steht im Code und muss stehen bleiben — auf ihn stuetzt
        sich die Aenderung oben."""
        block = ROUTER.split("async def _coerce_task_model_for_agent", 1)[1][:1400]
        self.assertIn("falls back to its own", block)
        self.assertIn("if not model:", block)

    def test_the_router_only_gets_asked_without_a_model(self):
        """Genau deshalb war er bei Delegation wirkungslos, solange immer eines
        mitkam."""
        self.assertIn("if model is None:", ROUTER)
        block = ROUTER.split("if model is None:", 1)[1][:200]
        self.assertIn("_route_model_by_content", block)


if __name__ == "__main__":
    unittest.main()
