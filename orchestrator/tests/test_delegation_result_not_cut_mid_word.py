"""Die Fertigmeldung eines delegierten Auftrags darf nicht mitten im Wort enden.

Kundenbeobachtung 2026-08-13: ein Team-Lead delegierte vier "gib exakt 'Hallo
Welt' aus"-Testauftraege. Drei von vier Rueckmeldungen brachen mitten im Wort
ab ("...reinen Au", "...I", "...lc"), noch bevor der eigentliche Satz "Hallo
Welt" im sichtbaren Text auftauchte — obwohl der Sub-Agent ihn vermutlich
ausgegeben hatte. Ursache: ``_notify_delegating_agent`` kuerzte das Ergebnis
zweimal mit einem blossen ``text[:n]`` (erst auf 800, dann nochmal auf 300
Zeichen), ohne auf Wortgrenzen zu achten.

Gleiches Muster steckte in den zwei anderen Harnesses (Claude-Code-MCP-Server,
Custom-LLM-API-Client) an der jeweiligen Status-Abfrage — beide geprueft ueber
den Dateiinhalt, weil sie aus dieser Python-Testsammlung nicht importierbar
sind (JS bzw. eigenes Agent-Package).
"""

import inspect
import unittest
from pathlib import Path

from app.core import task_router

REPO = Path(__file__).resolve().parents[2]


class OrchestratorCallbackTests(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(task_router.TaskRouter._notify_delegating_agent)

    def test_uses_the_word_boundary_safe_helper(self):
        self.assertIn("truncate_preserving_words(", self.src)

    def test_the_old_double_truncation_is_gone(self):
        # Erst [:800], dann nochmal [:300] auf demselben Wert — genau das hat
        # "Hallo Welt" aus dem sichtbaren Text geschnitten.
        self.assertNotIn("result_preview[:300]", self.src)
        self.assertNotIn('"No output")[:800]', self.src)

    def test_the_report_request_survives(self):
        # Regression gegen test_delegation_report_reaches_the_user.py: der Fix
        # darf die Aufforderung an den Lead nicht mit wegkuerzen.
        self.assertIn("Berichte dem Menschen", self.src)


class ClaudeCodeHarnessTests(unittest.TestCase):
    """agent/mcp/orchestrator-server.mjs — kein Python-Import moeglich, daher
    ueber den Dateiinhalt geprueft."""

    SRC = (REPO / "agent/mcp/orchestrator-server.mjs").read_text()

    def test_get_tasks_status_uses_the_word_boundary_safe_helper(self):
        self.assertIn("truncatePreservingWords(t.result", self.SRC)

    def test_the_old_raw_substring_cut_is_gone(self):
        self.assertNotIn("t.result.substring(0, 300)", self.SRC)

    def test_delegate_and_wait_still_returns_the_full_result_untruncated(self):
        # Der eigentliche Delegationsweg hat nie gekuerzt — das soll so bleiben,
        # nicht als Nebenwirkung dieses Fixes eingeschraenkt werden.
        block = self.SRC.split('case "delegate_and_wait"', 1)[1][:2200]
        self.assertIn('${r.result}', block)


class CustomLlmHarnessTests(unittest.TestCase):
    """agent/app/tools/api_client.py — eigenes Agent-Package, hier nur ueber
    den Dateiinhalt geprueft (wie test_delegation_report_reaches_the_user.py
    es fuer denselben Bereich schon macht)."""

    SRC = (REPO / "agent/app/tools/api_client.py").read_text()

    def test_delegate_and_wait_uses_the_word_boundary_safe_helper(self):
        block = self.SRC.split("async def delegate_and_wait(", 1)[1]
        block = block.split("\n    async def ", 1)[0]  # nur diese Methode
        self.assertIn("_truncate_preserving_words(", block)

    def test_the_old_raw_slice_is_gone(self):
        self.assertNotIn("or '(keine Ausgabe)')[:1500]", self.SRC)


if __name__ == "__main__":
    unittest.main()
