"""Beim Agenten-Update muss die Anleitungsdatei in JEDER Laufzeit frisch geschrieben werden.

Gefunden beim Ausrollen von v1.132.x: `update_agent` schrieb die Instruktionen nur,
wenn `mode == "claude_code"`. Auf dem Pi laufen 7 von 9 Agenten auf Codex — die
behielten die Anleitung, mit der sie erstellt wurden. Jede spaetere Verbesserung an
`DEFAULT_CLAUDE_MD` ging still an ihnen vorbei, egal wie oft man "Update" drueckte
(nachgewiesen: AGENT.md 16284 Bytes alt vs. CLAUDE.md 20276 Bytes neu).

Geprueft wird der Dateiname pro Modus und dass ueberhaupt geschrieben wird.
"""

import inspect
import re
import unittest

from app.core.agent_manager import AgentManager, DEFAULT_CLAUDE_MD


def _update_agent_source() -> str:
    return inspect.getsource(AgentManager.update_agent)


class InstructionsRefreshTests(unittest.TestCase):
    def test_refresh_is_not_gated_on_claude_code(self):
        """Der eigentliche Fehler: `if mode == "claude_code":` um den Schreibvorgang."""
        src = _update_agent_source()
        write_idx = src.find("_instructions_file")
        self.assertGreater(write_idx, 0, "Instruktions-Schreibvorgang nicht gefunden")
        # Kein Modus-Gate unmittelbar vor dem Schreiben
        before = src[:write_idx]
        last_gate = before.rfind('if mode == "claude_code":')
        if last_gate != -1:
            between = before[last_gate:]
            self.assertNotIn(
                "write_file_in_container", between,
                "Instruktionsdatei haengt wieder an einem claude_code-Gate",
            )

    def test_both_target_filenames_are_present(self):
        src = _update_agent_source()
        self.assertIn("/workspace/CLAUDE.md", src)
        self.assertIn("/workspace/AGENT.md", src)

    def test_target_file_is_chosen_by_mode(self):
        """CLAUDE.md nur fuer claude_code, sonst AGENT.md — Codex liest CLAUDE.md nicht."""
        src = _update_agent_source()
        m = re.search(
            r'_instructions_file\s*=\s*"(/workspace/CLAUDE\.md)"\s*if\s*mode\s*==\s*"claude_code"\s*else\s*"(/workspace/AGENT\.md)"',
            src,
        )
        self.assertIsNotNone(m, "Modus-abhaengige Dateiwahl nicht in der erwarteten Form")

    def test_written_content_comes_from_the_shared_template(self):
        src = _update_agent_source()
        self.assertIn("_render_claude_md(", src)

    def test_template_carries_the_cross_runtime_guidance(self):
        """Die Anleitung, die alle Laufzeiten erreichen muss (#475, #477)."""
        for needle in ("computer_open_app", "computer_screenshot", "brain_related", "brain_get"):
            self.assertIn(needle, DEFAULT_CLAUDE_MD, f"{needle} fehlt in DEFAULT_CLAUDE_MD")


if __name__ == "__main__":
    unittest.main()
