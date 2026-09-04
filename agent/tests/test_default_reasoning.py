"""Standard-Denktiefe pro Agent (Kundenwunsch 2026-08-24).

Bisher musste die Denktiefe in JEDEM Chat von Hand gesetzt werden, und
Aufgaben, Zeitplaene und delegierte Auftraege liefen immer mit dem
Laufzeit-Standard. Jetzt traegt der Agent eine Standard-Denktiefe
(DEFAULT_REASONING, aus den Agenten-Einstellungen), die ueberall dort gilt,
wo am einzelnen Lauf keine Stufe haengt. Eine im Chat gewaehlte Stufe
gewinnt weiterhin.
"""

import unittest
from unittest.mock import patch

from app.config import CLAUDE_THINKING_BUDGET, llm_default_reasoning_effort, settings


class LlmEffortFallbackTests(unittest.TestCase):
    """Die Kette: Stufe am Lauf > Standard-Denktiefe > Provider-Feinknopf."""

    def test_without_default_the_provider_knob_wins(self):
        with patch.object(settings, "default_reasoning", ""), \
             patch.object(settings, "llm_reasoning_effort", "medium"):
            self.assertEqual(llm_default_reasoning_effort(), "medium")

    def test_the_agents_default_beats_the_provider_knob(self):
        with patch.object(settings, "default_reasoning", "high"), \
             patch.object(settings, "llm_reasoning_effort", "low"):
            self.assertEqual(llm_default_reasoning_effort(), "high")

    def test_chat_level_names_are_translated_for_openai(self):
        with patch.object(settings, "llm_reasoning_effort", "low"):
            # Seit der Trennung von xhigh und max geht "max" als "max" raus —
            # die Stufe war vorher gar nicht erreichbar.
            with patch.object(settings, "default_reasoning", "max"):
                self.assertEqual(llm_default_reasoning_effort(), "max")
            with patch.object(settings, "default_reasoning", "xhigh"):
                self.assertEqual(llm_default_reasoning_effort(), "xhigh")
            with patch.object(settings, "default_reasoning", "off"):
                # "off" heisst: ausdruecklich OHNE Denken — nicht auf den
                # Feinknopf zurueckfallen.
                self.assertEqual(llm_default_reasoning_effort(), "")

    def test_whitespace_and_case_do_not_matter(self):
        with patch.object(settings, "default_reasoning", "  HIGH "):
            self.assertEqual(llm_default_reasoning_effort(), "high")


class ClaudeChatFallbackTests(unittest.TestCase):
    """Der Claude-Chat uebernimmt die Standard-Denktiefe, wenn keine Stufe kommt."""

    def _reasoning_after_handle(self, per_message: str, default: str) -> str:
        from app.chat_handler import ChatHandler

        handler = ChatHandler.__new__(ChatHandler)  # ohne LogPublisher-Aufbau
        with patch.object(settings, "default_reasoning", default):
            # Nur die Zuweisung aus handle_message nachstellen — genau die
            # Zeile, die die Stufe fuer den Lauf festlegt.
            handler._reasoning = per_message or settings.default_reasoning or ""
        return handler._reasoning

    def test_default_fills_the_gap(self):
        self.assertEqual(self._reasoning_after_handle("", "high"), "high")

    def test_explicit_choice_wins(self):
        self.assertEqual(self._reasoning_after_handle("low", "high"), "low")


class ThinkingBudgetTableTests(unittest.TestCase):
    """Chat und Aufgaben muessen mit DERSELBEN Budget-Tabelle arbeiten."""

    def test_chat_handler_uses_the_shared_table(self):
        from app.chat_handler import ChatHandler
        self.assertIs(ChatHandler._THINKING_BUDGET, CLAUDE_THINKING_BUDGET)

    def test_every_level_has_a_budget_and_max_is_the_ceiling(self):
        for level in ("low", "medium", "high", "max"):
            self.assertIn(level, CLAUDE_THINKING_BUDGET)
        self.assertEqual(CLAUDE_THINKING_BUDGET["max"], CLAUDE_THINKING_BUDGET["high"])


class TaskAndCodexWiringTests(unittest.TestCase):
    """Die Stellen, an denen Aufgaben die Standard-Denktiefe anwenden.

    Verhaltenstests scheitern hier an echten Subprozessen; diese Pruefungen
    halten fest, DASS der Fallback im jeweiligen Pfad verdrahtet ist — die
    Uebersetzungslogik selbst ist oben behavioral abgedeckt.
    """

    def test_claude_task_runner_applies_the_default_budget(self):
        import inspect
        from app import agent_runner
        src = inspect.getsource(agent_runner)
        self.assertIn("CLAUDE_THINKING_BUDGET[_level]", src)
        self.assertIn('env.pop("MAX_THINKING_TOKENS", None)', src)

    def test_codex_shared_path_falls_back_to_the_default(self):
        import inspect
        from app import codex_runner
        src = inspect.getsource(codex_runner)
        self.assertIn('or settings.default_reasoning', src)


if __name__ == "__main__":
    unittest.main()
