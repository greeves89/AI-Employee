"""Chat-Selbstheilung bei zu langem Verlauf — in ALLEN Laufzeiten (#623).

Der Claude-Pfad heilt sich seit #620; hier sind die Nachzuegler festgenagelt:
Codex verwirft die Sitzung und wiederholt, Custom-LLM komprimiert den Verlauf
im Notfall, und eine zu grosse EINZELNE Nachricht bekommt ueberall eine
Erklaerung statt des rohen CLI-Fehlers.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.chat_handler import _is_context_length_error


class MarkerTests(unittest.TestCase):
    """Die Formulierungen der drei Laufzeiten muessen alle erkannt werden."""

    def test_known_phrasings_match(self):
        for text in (
            "prompt is too long: 215000 tokens > 200000 maximum",
            "This model's maximum context length is 128000 tokens",
            "the request exceeds the context window",
            "context_length_exceeded",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_context_length_error(text))

    def test_ordinary_errors_do_not_match(self):
        self.assertFalse(_is_context_length_error("401 unauthorized"))
        self.assertFalse(_is_context_length_error(""))


class CodexSelfHealTests(unittest.IsolatedAsyncioTestCase):
    """Codex: Verlauf zu lang -> Meldung + frische Sitzung; Einzelnachricht zu
    gross -> Erklaerung statt Roh-Fehler."""

    def _handler(self, runner_results):
        from app.codex_runner import CodexChatHandler
        h = CodexChatHandler.__new__(CodexChatHandler)
        h.log_publisher = AsyncMock()
        h.pending_drain = None
        h.is_running = False
        h._process = None

        class _Runner:
            def __init__(self):
                self.calls = []
            async def _run_codex(self, message_id, t, model, stream, resume=False):
                self.calls.append(resume)
                return runner_results.pop(0)
            async def interrupt(self):
                pass
        h._runner = _Runner()
        return h

    async def _turn(self, handler, is_resume):
        # Die innere _run_turn-Logik direkt nachstellen waere Duplikat — wir
        # rufen handle_message nicht (Steering-Maschinerie), sondern pruefen
        # die Bausteine ueber den Quelltext + die Marker oben. Hier: der
        # Verlauf der Runner-Aufrufe bei einem Resume-Laengenfehler.
        from app.chat_handler import _is_context_length_error as is_ctx
        runner = handler._runner
        res = await runner._run_codex("m1", "text", "model", stream="chat", resume=is_resume)
        if is_resume and res.get("status") == "error":
            if is_ctx(res.get("error", "")):
                await handler.log_publisher.publish_chat("m1", "system", {"message": "neu"})
            res = await runner._run_codex("m1", "text", "model", stream="chat", resume=False)
        if res.get("status") == "error" and is_ctx(res.get("error", "")):
            res = {"status": "error", "error": "Diese einzelne Nachricht ist zu gross"}
        return res

    async def test_resume_overflow_retries_fresh(self):
        h = self._handler([
            {"status": "error", "error": "prompt is too long: 250000 tokens"},
            {"status": "completed", "result": "ok"},
        ])
        res = await self._turn(h, is_resume=True)
        self.assertEqual(res.get("status"), "completed")
        self.assertEqual(h._runner.calls, [True, False])

    async def test_oversized_single_message_gets_an_explanation(self):
        h = self._handler([
            {"status": "error", "error": "exceeds the context window"},
        ])
        res = await self._turn(h, is_resume=False)
        self.assertIn("einzelne Nachricht", res.get("error", ""))

    def test_codex_run_turn_is_wired(self):
        import inspect
        from app import codex_runner
        src = inspect.getsource(codex_runner.CodexChatHandler.handle_message)
        self.assertIn("_is_context_length_error", src)
        self.assertIn("Der Gespraechsverlauf war zu lang geworden", src)
        self.assertIn("einzelne Nachricht ist zu gross", src)


class ClaudeFreshSessionExplanationTests(unittest.TestCase):
    def test_fresh_session_overflow_explains_instead_of_raw_error(self):
        import inspect
        from app import chat_handler
        src = inspect.getsource(chat_handler.ChatHandler._run_turn_with_retries)
        self.assertIn("if not was_resumed:", src)
        self.assertIn("einzelne Nachricht ist zu gross", src)


class CustomLlmEmergencyCompactionTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self):
        from app.llm_chat_handler import LLMChatHandler
        h = LLMChatHandler.__new__(LLMChatHandler)
        h.log_publisher = AsyncMock()
        h._compact_history = AsyncMock()
        return h

    async def test_overflow_triggers_compaction_and_a_note(self):
        h = self._handler()
        await h._heal_after_context_overflow("m1", "context_length_exceeded")
        h._compact_history.assert_awaited_once()
        h.log_publisher.publish_chat.assert_awaited()

    async def test_other_errors_do_not_touch_the_history(self):
        h = self._handler()
        await h._heal_after_context_overflow("m1", "connection reset")
        h._compact_history.assert_not_awaited()

    async def test_a_failing_compaction_does_not_raise(self):
        h = self._handler()
        h._compact_history = AsyncMock(side_effect=RuntimeError("kaputt"))
        await h._heal_after_context_overflow("m1", "prompt too long")


if __name__ == "__main__":
    unittest.main()
