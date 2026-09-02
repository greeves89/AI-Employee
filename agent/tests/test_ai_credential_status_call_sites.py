"""Die Statusmeldung muss auf den Wegen ankommen, die der Nutzer wirklich benutzt.

Die vorhandene Testreihe deckt den Aufgaben-Weg von ``AgentRunner`` ab und die
Einordnung in ``report_result_status`` selbst. Beides ist richtig, beweist aber
nichts ueber die uebrigen Aufrufstellen: ein Chat-Zug und ein Codex-Lauf koennen
an genau demselben Zugangsfehler scheitern, ohne dass der Zugang je rot wird.

Geprueft wird deshalb pro Weg, WAS gemeldet wird — nicht, dass der Aufruf im
Quelltext steht.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

AUTH_FEHLER = {"status": "error", "error": "401 Unauthorized: token_expired"}
ERFOLG = {"status": "completed", "result": "fertig"}


class ChatWegMeldetStatus(unittest.IsolatedAsyncioTestCase):
    def _handler(self):
        from app.chat_handler import ChatHandler

        publisher = MagicMock()
        publisher.publish_chat = AsyncMock()
        publisher.publish = AsyncMock()
        return ChatHandler(log_publisher=publisher)

    async def test_erfolgreicher_chatzug_meldet_ok(self):
        handler = self._handler()
        handler._execute_cli = AsyncMock(return_value=dict(ERFOLG))

        with patch("app.chat_handler.report_result_status", AsyncMock()) as melde:
            await handler._run_turn_with_retries("m1", "hallo", "sonnet")

        melde.assert_awaited_once_with(ERFOLG)

    async def test_zugangsfehler_im_chat_meldet_das_ergebnis_der_wiederholung(self):
        """Nach einem Auth-Fehler wird der Zug wiederholt.

        Gemeldet werden muss das Ergebnis der WIEDERHOLUNG. Wuerde der urspruengliche
        Fehler gemeldet, bliebe ein Zugang rot, den die Plattform gerade erneuert hat
        — der Nutzer sieht einen Defekt, der schon behoben ist.
        """
        handler = self._handler()
        handler._execute_cli = AsyncMock(side_effect=[dict(AUTH_FEHLER), dict(ERFOLG)])

        with patch("app.chat_handler.report_result_status", AsyncMock()) as melde, \
             patch("app.config.wait_for_new_oauth_token", AsyncMock()):
            await handler._run_turn_with_retries("m1", "hallo", "sonnet")

        melde.assert_awaited_once_with(ERFOLG)

    async def test_bleibender_zugangsfehler_im_chat_meldet_auth_failed(self):
        handler = self._handler()
        handler._execute_cli = AsyncMock(return_value=dict(AUTH_FEHLER))

        with patch("app.chat_handler.report_result_status", AsyncMock()) as melde, \
             patch("app.config.wait_for_new_oauth_token", AsyncMock()):
            await handler._run_turn_with_retries("m1", "hallo", "sonnet")

        melde.assert_awaited_once_with(AUTH_FEHLER)


class CodexWegMeldetStatus(unittest.IsolatedAsyncioTestCase):
    def _runner(self):
        from app.codex_runner import CodexAgentRunner

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        publisher.publish_chat = AsyncMock()
        return CodexAgentRunner(log_publisher=publisher)

    async def test_codex_aufgabe_meldet_zugangsfehler(self):
        runner = self._runner()
        runner._run_codex = AsyncMock(return_value=dict(AUTH_FEHLER))

        with patch("app.codex_runner.report_result_status", AsyncMock()) as melde, \
             patch("app.codex_runner.compose_prompt_bundle", return_value=""):
            await runner.execute_task("t1", "mach was")

        melde.assert_awaited_once_with(AUTH_FEHLER)

    async def test_codex_aufgabe_meldet_erfolg(self):
        runner = self._runner()
        runner._run_codex = AsyncMock(return_value=dict(ERFOLG))

        with patch("app.codex_runner.report_result_status", AsyncMock()) as melde, \
             patch("app.codex_runner.compose_prompt_bundle", return_value=""):
            await runner.execute_task("t1", "mach was")

        melde.assert_awaited_once_with(ERFOLG)


class CodexChatWegMeldetStatus(unittest.IsolatedAsyncioTestCase):
    """Der Codex-CHAT-Weg ist eine andere Aufrufstelle als der Codex-AUFGABEN-Weg.

    ``CodexAgentRunner.execute_task`` und ``CodexChatHandler.handle_message`` melden
    getrennt. Ein Test auf den Aufgaben-Weg laesst den Chat-Weg unbewiesen — die
    Luecke faellt nicht auf, weil beide im selben Modul liegen und dieselbe Funktion
    aufrufen.
    """

    def _handler(self):
        from app.codex_runner import CodexChatHandler

        publisher = MagicMock()
        publisher.publish = AsyncMock()
        publisher.publish_chat = AsyncMock()
        return CodexChatHandler(log_publisher=publisher)

    async def _melde_bei(self, ergebnis):
        handler = self._handler()
        with patch("app.codex_runner.report_result_status", AsyncMock()) as melde, \
             patch("app.steering.run_turns_with_steering",
                   AsyncMock(return_value=dict(ergebnis))):
            await handler.handle_message("m1", "hallo")
        return melde

    async def test_codex_chatzug_meldet_zugangsfehler(self):
        melde = await self._melde_bei(AUTH_FEHLER)
        melde.assert_awaited_once_with(AUTH_FEHLER)

    async def test_erfolgreicher_codex_chatzug_meldet_ok(self):
        melde = await self._melde_bei(ERFOLG)
        melde.assert_awaited_once_with(ERFOLG)


if __name__ == "__main__":
    unittest.main()
