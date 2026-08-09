"""Werkzeug-Nutzung sichtbar machen.

„Mir ist das noch zu intransparent mit der Tool-Nutzung. Ich denke immer, der hat dann
nichts gemacht." — Der Sprachfront rief Werkzeuge auf, ohne dass davon etwas zu sehen war;
gesagt wurde nur das Ergebnis in Prosa. Jeder Aufruf erscheint jetzt rechts mit Namen,
Eingabe und Ergebnis.
"""

import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
VOICE = (ORCH / "app/services/realtime_voice_session.py").read_text()
UI = (ORCH.parent / "frontend/src/components/agents/voice-session.tsx").read_text()


class BackendTests(unittest.TestCase):
    def test_call_is_announced_with_its_arguments(self):
        self.assertIn('"type": "tool_call"', VOICE)
        self.assertIn("_short_args(args)", VOICE)

    def test_result_is_announced(self):
        self.assertIn('"type": "tool_result"', VOICE)
        respond = VOICE.split("async def _respond", 1)[1].split("async def _handle_tool_use", 1)[0]
        self.assertIn("self._tool_calls.pop(tool_use_id", respond)

    def test_arguments_are_shortened_not_dumped(self):
        """Ein Werkzeug-Argument kann ein ganzer Prompt sein — in der Spur reicht ein Blick."""
        self.assertIn("def _short_args(", VOICE)
        self.assertIn("[:60]", VOICE)

    def test_every_tool_goes_through_the_same_gate(self):
        """Die Spur haengt am zentralen Einstieg, nicht an einzelnen Werkzeugen —
        sonst fehlt beim naechsten neuen Werkzeug wieder die Anzeige."""
        head = VOICE.split("async def _handle_tool_use", 1)[1][:1200]
        self.assertIn("self._tool_calls[tool_use_id]", head)


class UiTests(unittest.TestCase):
    def test_events_are_consumed(self):
        self.assertIn('case "tool_call":', UI)
        self.assertIn('case "tool_result":', UI)

    def test_pending_and_done_look_different(self):
        self.assertIn("toolLog", UI)
        self.assertIn("animate-spin", UI.split("toolLog.slice", 1)[1][:900])

    def test_input_and_output_are_shown(self):
        pane = UI.split("toolLog.slice", 1)[1][:1200]
        self.assertIn("t.input", pane)
        self.assertIn("t.output", pane)


if __name__ == "__main__":
    unittest.main()
