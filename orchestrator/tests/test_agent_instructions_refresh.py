"""Beim Agenten-Update muss die Anleitungsdatei in JEDER Laufzeit frisch geschrieben werden.

Gefunden beim Ausrollen von v1.132.x: `update_agent` schrieb die Instruktionen nur,
wenn `mode == "claude_code"`. Auf dem Pi laufen 7 von 9 Agenten auf Codex — die behielten
die Anleitung, mit der sie erstellt wurden. Jede spaetere Verbesserung an
`DEFAULT_CLAUDE_MD` ging still an ihnen vorbei, egal wie oft man "Update" drueckte
(nachgewiesen: AGENT.md 16284 Bytes alt vs. CLAUDE.md 20276 Bytes neu).

Beim Kunden (SKBS) laufen die Azure-Modelle als `custom_llm` — dieselbe Falle. Deshalb
wird hier das VERHALTEN der Pfadwahl geprueft, nicht der Quelltext.
"""

import inspect
import unittest
from unittest.mock import MagicMock

from app.core.agent_manager import AgentManager, DEFAULT_CLAUDE_MD, instructions_path


class InstructionsPathTests(unittest.TestCase):
    """Die Pfadwahl selbst — eine Funktion, alle Laufzeiten."""

    def test_claude_code_gets_claude_md(self):
        self.assertEqual(instructions_path("claude_code"), "/workspace/CLAUDE.md")

    def test_codex_gets_agent_md(self):
        self.assertEqual(instructions_path("codex_cli"), "/workspace/AGENT.md")

    def test_custom_llm_gets_agent_md(self):
        """Der Fall beim Kunden: Azure-Modelle laufen als custom_llm."""
        self.assertEqual(instructions_path("custom_llm"), "/workspace/AGENT.md")

    def test_unknown_mode_still_gets_a_file(self):
        """Eine neue Laufzeit darf nicht stillschweigend ganz ohne Anleitung dastehen —
        genau so ist der urspruengliche Fehler entstanden."""
        for mode in ("some_future_runtime", "", None):
            self.assertEqual(instructions_path(mode), "/workspace/AGENT.md")

    def test_only_claude_code_maps_to_claude_md(self):
        """Kein Beinahe-Treffer darf CLAUDE.md abbekommen."""
        for mode in ("claude", "claude_code_v2", "CLAUDE_CODE"):
            self.assertEqual(instructions_path(mode), "/workspace/AGENT.md")


class UpdateAgentWiringTests(unittest.TestCase):
    """Dass `update_agent` diese Pfadwahl auch wirklich benutzt — und ohne Modus-Gate."""

    def setUp(self):
        self.src = inspect.getsource(AgentManager.update_agent)

    def test_update_uses_the_shared_path_helper(self):
        self.assertIn("instructions_path(mode)", self.src)

    def test_write_is_not_gated_on_a_mode(self):
        """Der eigentliche Fehler war ein `if mode == "claude_code":` um den Schreibvorgang."""
        idx = self.src.find("instructions_path(mode)")
        self.assertGreater(idx, 0)
        before = self.src[:idx]
        gate = before.rfind('if mode == "claude_code"')
        if gate != -1:
            self.assertNotIn(
                "write_file_in_container", before[gate:],
                "Instruktionsdatei haengt wieder an einem Modus-Gate",
            )

    def test_content_comes_from_the_shared_template(self):
        self.assertIn("_render_claude_md(", self.src)


class TemplateContentTests(unittest.TestCase):
    def test_template_carries_the_cross_runtime_guidance(self):
        """Die Anleitung, die JEDE Laufzeit erreichen muss (#475, #477)."""
        for needle in ("computer_open_app", "computer_screenshot", "brain_related", "brain_get"):
            self.assertIn(needle, DEFAULT_CLAUDE_MD, f"{needle} fehlt in DEFAULT_CLAUDE_MD")

    def test_recreate_path_writes_the_same_way(self):
        """Es gibt zwei Wege, die einen Container neu bauen — beide muessen die
        Anleitung gleich behandeln, sonst driftet wieder einer weg."""
        src = inspect.getsource(AgentManager)
        self.assertEqual(
            src.count("instructions_path(mode)"), 2,
            "beide Recreate-Pfade muessen die gemeinsame Pfadwahl nutzen",
        )


if __name__ == "__main__":
    unittest.main()
