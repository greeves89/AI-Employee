"""Waehrend eines Telegram-Wake-Waits muss der Tipp-Indikator lebendig bleiben.

Nutzer-Beobachtung (Screenshot, 26.09.2026): eine Nachricht erscheint doppelt im
Chat, nachdem der Bot "Agent faehrt hoch, einen Moment..." geschickt hat. Der Wake-
Wait (``user_lifecycle.wake_agent(..., wait=True, timeout=20)``) kann bis zu 20 s
dauern und war komplett still — Telegrams Tipp-Indikator erlischt nach ~5 s ohne
Auffrischung. Ein Client, der 20 s lang gar kein Lebenszeichen sieht, haelt den
Versand fuer gescheitert und schickt die Nachricht erneut (mit neuer message_id,
die ``channel_gateway.already_seen`` deshalb nicht als Dublette erkennt).

Dieser Test haelt fest: waehrend eines laengeren Wakes wird der Tipp-Indikator
mehrfach aufgefrischt, nicht nur einmal am Anfang geschickt und dann Stille.
"""

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

import app.db.session as sess_mod
import app.services.docker_service as docker_mod
import app.services.user_lifecycle as lifecycle_mod
from app.models.agent import AgentState
from app.telegram.agent_bot import TelegramAgentBot


def _patched(agent, docker, wake_agent_fn):
    """Patcht alle drei innerhalb von ``_ensure_agent_running`` lokal importierten
    Fremdteile ueber die schon geladenen Modulobjekte — ``mock.patch("a.b.c", ...)``
    scheitert sonst mit 'module hat kein Attribut', solange das Untermodul noch
    nicht anderswo importiert wurde."""
    return (
        mock.patch.object(sess_mod, "async_session_factory", lambda: _Session(agent)),
        mock.patch.object(docker_mod, "DockerService", lambda: docker),
        mock.patch.object(lifecycle_mod, "wake_agent", wake_agent_fn),
    )


class _Session:
    def __init__(self, agent):
        self._agent = agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, _query):
        return self._agent

    async def commit(self):
        pass


class _Docker:
    def get_container_status(self, _container_id):
        return "exited"


class WakeTypingHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = TelegramAgentBot("a1", "TestAgent", "token", "key")
        # Kein echtes 4-Sekunden-Warten in einem Unit-Test.
        self.bot.WAKE_TYPING_INTERVAL_SECONDS = 0.02

    async def test_typing_is_refreshed_repeatedly_during_a_slow_wake(self):
        async def _slow_wake(_db, _docker, _agent_id, wait=True, timeout=20):
            await asyncio.sleep(0.09)  # laenger als mehrere Tipp-Intervalle
            return True

        agent = SimpleNamespace(id="a1", state=AgentState.STOPPED, container_id="c1")
        p1, p2, p3 = _patched(agent, _Docker(), _slow_wake)
        with p1, p2, p3:
            reply_text = AsyncMock()
            send_action = AsyncMock()
            update = SimpleNamespace(
                message=SimpleNamespace(reply_text=reply_text),
                effective_chat=SimpleNamespace(send_action=send_action),
            )
            ok = await self.bot._ensure_agent_running(update, "a1")

        self.assertTrue(ok)
        reply_text.assert_awaited_once_with("⏳ Agent fährt hoch, einen Moment...")
        self.assertGreaterEqual(
            send_action.await_count, 3,
            "Der Tipp-Indikator muss waehrend eines laengeren Wakes mehrfach "
            "aufgefrischt werden, sonst sieht der Client eine tote Leitung",
        )
        for call in send_action.await_args_list:
            self.assertEqual(call.args, ("typing",))

    async def test_a_fast_wake_still_returns_correctly_with_at_least_one_ping(self):
        async def _fast_wake(_db, _docker, _agent_id, wait=True, timeout=20):
            return True

        agent = SimpleNamespace(id="a1", state=AgentState.STOPPED, container_id="c1")
        p1, p2, p3 = _patched(agent, _Docker(), _fast_wake)
        with p1, p2, p3:
            reply_text = AsyncMock()
            send_action = AsyncMock()
            update = SimpleNamespace(
                message=SimpleNamespace(reply_text=reply_text),
                effective_chat=SimpleNamespace(send_action=send_action),
            )
            ok = await self.bot._ensure_agent_running(update, "a1")

        self.assertTrue(ok)
        self.assertGreaterEqual(send_action.await_count, 1)

    async def test_a_running_agent_needs_no_wake_and_no_typing(self):
        async def _should_not_be_called(*_a, **_kw):
            raise AssertionError("wake_agent darf bei einem laufenden Agenten nicht aufgerufen werden")

        agent = SimpleNamespace(id="a1", state=AgentState.RUNNING, container_id="c1")

        class _RunningDocker:
            def get_container_status(self, _container_id):
                return "running"

        p1, p2, p3 = _patched(agent, _RunningDocker(), _should_not_be_called)
        with p1, p2, p3:
            reply_text = AsyncMock()
            send_action = AsyncMock()
            update = SimpleNamespace(
                message=SimpleNamespace(reply_text=reply_text),
                effective_chat=SimpleNamespace(send_action=send_action),
            )
            ok = await self.bot._ensure_agent_running(update, "a1")

        self.assertFalse(ok)
        reply_text.assert_not_awaited()
        send_action.assert_not_awaited()

    async def test_a_failed_wake_is_reported_false_after_the_typing_loop(self):
        async def _wake_that_fails(_db, _docker, _agent_id, wait=True, timeout=20):
            await asyncio.sleep(0.05)
            return False

        agent = SimpleNamespace(id="a1", state=AgentState.STOPPED, container_id="c1")
        p1, p2, p3 = _patched(agent, _Docker(), _wake_that_fails)
        with p1, p2, p3:
            reply_text = AsyncMock()
            send_action = AsyncMock()
            update = SimpleNamespace(
                message=SimpleNamespace(reply_text=reply_text),
                effective_chat=SimpleNamespace(send_action=send_action),
            )
            ok = await self.bot._ensure_agent_running(update, "a1")

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
