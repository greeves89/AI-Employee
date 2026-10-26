"""Nur-ansehen-Zug (#1019): der Bridge-Beobachter zeigt dem Agenten ein
Bildschirmfoto — der Agent darf ANBIETEN, aber nicht handeln.

Ein Bildschirm voller fremder Inhalte (Webseite, Mail) darf ihn nicht dazu
bringen, Mails zu senden oder Befehle auszuführen. Jede Laufzeit setzt die
Grenze selbst; hier steht für alle drei, dass sie es tut.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.providers.base import ChatMessage, LLMEvent


def _werkzeug(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


class CodexGrenze(unittest.TestCase):
    def test_nur_ansehen_ist_lesend_und_ohne_mcp(self):
        from app.codex_runner import codex_rechte

        rechte = codex_rechte(True)
        self.assertIn("read-only", rechte)
        self.assertIn("mcp_servers={}", rechte)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", rechte)

    def test_normaler_zug_bleibt_wie_er_war(self):
        from app.codex_runner import codex_rechte

        self.assertEqual(codex_rechte(False), ["--dangerously-bypass-approvals-and-sandbox"])


class ClaudeGrenze(unittest.TestCase):
    def test_ansehen_darf_nur_lesen(self):
        from app.chat_handler import eingebaute_werkzeuge

        self.assertEqual(eingebaute_werkzeuge(False, True), "Read")

    def test_uebergabe_bleibt_ohne_werkzeuge(self):
        from app.chat_handler import eingebaute_werkzeuge

        self.assertEqual(eingebaute_werkzeuge(True, False), "")
        self.assertEqual(eingebaute_werkzeuge(True, True), "")


class GedaechtnisNurLesen(unittest.TestCase):
    def test_claude_sperrt_gedaechtnis_schreiben_nur_beim_ansehen(self):
        from app.chat_handler import gesperrte_werkzeuge

        self.assertEqual(gesperrte_werkzeuge(False), ["AskUserQuestion"])
        gesperrt = gesperrte_werkzeuge(True)
        self.assertIn("mcp__memory__memory_save", gesperrt)
        self.assertIn("mcp__memory__memory_delete", gesperrt)

    def test_custom_llm_liest_nur(self):
        from app.llm_chat_handler import NUR_ANSEHEN_WERKZEUGE

        self.assertIn("read_file", NUR_ANSEHEN_WERKZEUGE)
        for verboten in ("memory_save", "memory_delete", "write_file", "edit_file", "bash",
                         "web_fetch", "computer_use", "send_message"):
            self.assertNotIn(verboten, NUR_ANSEHEN_WERKZEUGE)


class _Publisher:
    async def publish_chat(self, message_id, kind, payload):
        pass


class _Anbieter:
    """Erst ein Werkzeugaufruf, dann die Antwort."""

    def __init__(self, werkzeug):
        self.werkzeug = werkzeug
        self.aufrufe = 0
        self.gesehene_werkzeuge = []
        self.reasoning_effort = ""

    def stream_completion(self, messages, tools=None):
        self.gesehene_werkzeuge.append([t["function"]["name"] for t in tools or []])
        self.aufrufe += 1
        erster = self.aufrufe == 1

        async def gen():
            if erster:
                yield LLMEvent(type="tool_call", tool_name=self.werkzeug, tool_id="t1",
                               tool_input={"to": "x@example.invalid"})
                yield LLMEvent(type="done")
            else:
                yield LLMEvent(type="text_delta", text="NICHTS")
                yield LLMEvent(type="done")

        return gen()


class CustomLlmGrenze(unittest.IsolatedAsyncioTestCase):
    KATALOG = [_werkzeug(n) for n in ("memory_search", "memory_list", "send_email", "bash", "computer_use")]

    def _handler(self):
        from app.llm_chat_handler import LLMChatHandler

        h = LLMChatHandler(log_publisher=_Publisher())
        h._context_window = 1_000_000
        h._history = [ChatMessage(role="system", content="S")]
        h._tool_executor = MagicMock()
        h._tool_executor.execute = AsyncMock(return_value="ok")
        return h

    async def _fahre(self, werkzeug, nur_ansehen):
        from app.config import settings

        h = self._handler()
        anbieter = _Anbieter(werkzeug)
        # Ein Blick beginnt ohne Verlauf und baut den Systemprompt neu — der liest
        # /workspace und ist hier nicht die Frage.
        with patch.object(h, "_get_provider", return_value=anbieter), \
             patch.object(h, "_get_catalog", new=AsyncMock(return_value=list(self.KATALOG))), \
             patch.object(h, "_verlauf_nachladen", new=AsyncMock(return_value=[])), \
             patch("app.runner_hooks.get_identity_context", return_value=""), \
             patch("app.runner_hooks.get_memory_preload", return_value=""), \
             patch("app.runner_hooks.get_skills_context", return_value=""), \
             patch("app.runner_hooks.get_mounts_context", return_value=""), \
             patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value=""), \
             patch.object(settings, "llm_tools_enabled", True), \
             patch.object(settings, "max_turns", 5):
            await h.handle_message("m1", "Was siehst du?", nur_ansehen=nur_ansehen)
        return h, anbieter

    async def test_sieht_nur_lesende_gedaechtniswerkzeuge(self):
        _, anbieter = await self._fahre("memory_search", True)
        self.assertEqual(set(anbieter.gesehene_werkzeuge[0]), {"memory_search", "memory_list"})

    async def test_erfundener_aufruf_erreicht_den_ausfuehrer_nicht(self):
        h, _ = await self._fahre("send_email", True)
        h._tool_executor.execute.assert_not_called()

    async def test_gedaechtnis_lesen_geht(self):
        h, _ = await self._fahre("memory_search", True)
        h._tool_executor.execute.assert_awaited()

    async def test_normaler_zug_unveraendert(self):
        h, _ = await self._fahre("send_email", False)
        h._tool_executor.execute.assert_awaited()
        self.assertFalse(h._nur_ansehen)

    async def test_jeder_blick_ohne_verlauf(self):
        from app.config import settings

        h = self._handler()
        h._history.append(ChatMessage(role="user", content="altes Bild"))
        with patch.object(h, "_get_provider", return_value=_Anbieter("memory_list")), \
             patch.object(h, "_get_catalog", new=AsyncMock(return_value=list(self.KATALOG))), \
             patch.object(h, "_verlauf_nachladen", new=AsyncMock(return_value=[])), \
             patch("app.runner_hooks.get_identity_context", return_value=""), \
             patch("app.runner_hooks.get_memory_preload", return_value=""), \
             patch("app.runner_hooks.get_skills_context", return_value=""), \
             patch("app.runner_hooks.get_mounts_context", return_value=""), \
             patch("app.runner_hooks.get_marketplace_skill_suggestions", return_value=""), \
             patch.object(settings, "llm_tools_enabled", True), \
             patch.object(settings, "max_turns", 5):
            await h.handle_message("m2", "neues Bild", nur_ansehen=True)
        self.assertNotIn("altes Bild", [m.content for m in h._history])


if __name__ == "__main__":
    unittest.main()
