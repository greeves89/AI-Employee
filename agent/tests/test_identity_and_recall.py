"""Identitaet und Gedaechtnis-Rueckweg in ALLEN custom_llm-Wegen.

Der Kunde fragte seinen Agenten „wie heisst du?" und bekam „ich habe keinen eigenen
Namen" — die CLI-Laufzeiten lesen ihre Anleitung von der Platte, der custom_llm-Weg baut
seinen Prompt selbst und las weder AGENT.md noch das Gedaechtnis.

Die Tests pruefen deshalb nicht nur, dass ``get_identity_context`` funktioniert, sondern
dass JEDER Einstiegspunkt sie auch benutzt — das Vorbeigehen selbst soll verboten sein.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from app import runner_hooks
from app.config import settings


class IdentityContextTests(unittest.TestCase):
    def test_name_and_role_appear(self):
        with patch.object(settings, "agent_name", "Mr. Data"), \
             patch.object(settings, "agent_role", "Datenanalyst"), \
             patch.object(runner_hooks, "_INSTRUCTIONS_MAX_CHARS", 100):
            out = runner_hooks.get_identity_context()
        self.assertIn("Mr. Data", out)
        self.assertIn("Datenanalyst", out)
        # Und die Regel, den Namen anzunehmen, wenn der Nutzer einen vergibt.
        self.assertIn("memory_save", out)

    def test_empty_without_name_role_or_file(self):
        """Kein Name, keine Rolle, keine Anleitung → leerer String, kein Rauschen."""
        with patch.object(settings, "agent_name", ""), \
             patch.object(settings, "agent_role", ""), \
             patch("builtins.open", side_effect=OSError):
            self.assertEqual(runner_hooks.get_identity_context(), "")

    def test_instruction_file_is_inlined_and_capped(self):
        long_text = "Zeile\n" * 5000
        with patch.object(settings, "agent_name", "A"), \
             patch.object(settings, "agent_role", ""), \
             patch("builtins.open", unittest.mock.mock_open(read_data=long_text)):
            out = runner_hooks.get_identity_context()
        self.assertIn("BETRIEBSANLEITUNG", out)
        self.assertIn("gekürzt", out)
        self.assertLess(len(out), runner_hooks._INSTRUCTIONS_MAX_CHARS + 2000)

    def test_agent_md_wins_over_claude_md(self):
        """AGENT.md ist die modusunabhaengige Anleitung — sie hat Vorrang."""
        opened: list[str] = []

        def _fake_open(path, *a, **kw):
            opened.append(str(path))
            raise OSError

        with patch.object(settings, "agent_name", ""), patch.object(settings, "agent_role", ""), \
             patch("builtins.open", _fake_open):
            runner_hooks.get_identity_context()
        self.assertEqual(opened[0], "/workspace/AGENT.md")


class EveryEntryPointUsesItTests(unittest.TestCase):
    """Quelltext-Pruefung: jeder custom_llm-Einstieg zieht Identitaet UND Gedaechtnis.

    Absichtlich am Quelltext und nicht am Verhalten — es geht genau darum, dass kein
    NEUER Weg still daran vorbeigebaut wird.
    """

    @staticmethod
    def _src(rel: str) -> str:
        return (Path(__file__).resolve().parents[1] / rel).read_text()

    def test_chat_path(self):
        src = self._src("app/llm_chat_handler.py")
        self.assertIn("get_identity_context()", src)
        # get_memory_preload( statt exaktem "()" — Issue #547 gibt ihr optional den
        # Task-/Chat-Text als task_context mit, der Aufruf selbst darf nicht fehlen.
        self.assertIn("get_memory_preload(", src)

    def test_task_path_including_lightweight(self):
        src = self._src("app/llm_runner.py")
        self.assertIn("get_identity_context()", src)
        # Der leichte Zweig (Chat/Telegram) hatte KEINEN Preload — genau das war die Luecke.
        light = src.split("if lightweight:", 1)[1].split("else:", 1)[0]
        self.assertIn("get_memory_preload(", light)

    def test_agent_to_agent_path(self):
        self.assertIn("get_identity_context()", self._src("app/message_consumer.py"))


if __name__ == "__main__":
    unittest.main()
