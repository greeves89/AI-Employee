"""Helfer behalten ihre Kernfelder im Verlauf — auch Delegationen.

Auf einer Kundenanlage stand nach dem Neuladen „Delegierter Auftrag — an anderen
Agenten · an anderen Agenten" mit „(kein Auftragstext übermittelt)": Die
Werkzeug-Eingabe wird auf 200 Zeichen gekuerzt gespeichert, und nur eigene
Subagenten bekamen ihre Felder gesondert.
"""
import unittest

from app.subagent_felder import subagent_felder


class SubagentFelderTests(unittest.TestCase):
    def test_delegation_mit_mcp_vorsilbe(self):
        f = subagent_felder("mcp__orchestrator__create_task",
                            {"title": "Blocker beheben", "prompt": "x" * 5000, "agent_id": "abcdef123456"})
        self.assertEqual(f["description"], "Blocker beheben")
        self.assertEqual(len(f["prompt"]), 4000)
        self.assertEqual(f["subagent_type"], "an abcdef12")
        self.assertTrue(f["run_in_background"])

    def test_warten_auf_delegation_ist_nicht_im_hintergrund(self):
        self.assertFalse(subagent_felder("delegate_and_wait", {"title": "t"})["run_in_background"])

    def test_stapel(self):
        f = subagent_felder("create_task_batch", {"tasks": [
            {"title": "A", "prompt": "a"}, {"title": "B", "prompt": "b"}]})
        self.assertEqual(f["description"], "2 Aufträge delegiert")
        self.assertIn("1. A\na", f["prompt"])
        self.assertIsNone(f["subagent_type"])

    def test_eigener_subagent(self):
        f = subagent_felder("Agent", {"description": "Recherche", "prompt": "p", "subagent_type": "Explore"})
        self.assertEqual((f["description"], f["subagent_type"], f["prompt"]), ("Recherche", "Explore", "p"))

    def test_gewoehnliches_werkzeug(self):
        self.assertIsNone(subagent_felder("Bash", {"command": "ls"}))
        self.assertIsNone(subagent_felder("mcp__orchestrator__list_todos", {}))


if __name__ == "__main__":
    unittest.main()
