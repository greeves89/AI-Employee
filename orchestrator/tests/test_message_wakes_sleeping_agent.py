"""Eine Nachricht an einen schlafenden Agenten muss ihn wecken.

Am 2026-08-12 schickte der Team-Lead auf der Kundenanlage sieben Agenten je ein
„Hallo Welt". Alle sieben Nachrichten stehen in ``agent_messages``, die
Zustellung meldete „sent" — und keine einzige Antwort kam. Grund: die Empfaenger
waren Minuten vorher idle ausgestiegen. ``agent:{id}:messages`` wird aber **nur
gelesen, solange der Container laeuft**. Die Nachrichten lagen in Warteschlangen,
die niemand liest.

Der Lead meldete daraufhin korrekt „keine Rueckmeldung" — von aussen sah es aus,
als koennten die Agenten grundsaetzlich nicht miteinander reden.

Fuer Besprechungen war das Aufwecken laengst geloest, fuer Nachrichten nicht.
Dieser Test haelt beides fest: dass geweckt wird, und dass **vor** dem Einreihen
geweckt wird — danach zu wecken hilft nur zufaellig.
"""

import unittest
from types import SimpleNamespace

from app.core import agent_wakeup


class _Docker:
    def __init__(self, status: str):
        self._status = status
        self.asked: list[str] = []

    def get_container_status(self, container_id: str) -> str:
        self.asked.append(container_id)
        return self._status


class _Manager:
    """Statt eines echten AgentManager — merkt sich nur, wer gestartet wurde."""

    started: list[str] = []

    def __init__(self, db, docker, redis):
        pass

    async def start_agent(self, agent_id: str):
        _Manager.started.append(agent_id)
        return SimpleNamespace(id=agent_id)


class _Session:
    def __init__(self, agent):
        self._agent = agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, _query):
        return self._agent


def _patch(monkey_agent, docker_status: str):
    """Haengt die Fremdteile (DB, AgentManager) an Fakes."""
    import app.core.agent_manager as am_mod
    import app.db.session as sess_mod

    _Manager.started = []
    orig_mgr = am_mod.AgentManager
    orig_factory = sess_mod.async_session_factory
    am_mod.AgentManager = _Manager
    sess_mod.async_session_factory = lambda: _Session(monkey_agent)
    return orig_mgr, orig_factory, _Docker(docker_status)


def _restore(orig_mgr, orig_factory):
    import app.core.agent_manager as am_mod
    import app.db.session as sess_mod

    am_mod.AgentManager = orig_mgr
    sess_mod.async_session_factory = orig_factory


class EnsureAgentRunningTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_stopped_agent_is_started(self):
        agent = SimpleNamespace(id="a1", container_id="c1")
        orig = _patch(agent, "exited")
        try:
            ok = await agent_wakeup.ensure_agent_running("a1", orig[2], redis=None)
        finally:
            _restore(orig[0], orig[1])
        self.assertTrue(ok)
        self.assertEqual(_Manager.started, ["a1"],
                         "Der schlafende Agent wurde nicht geweckt — seine "
                         "Warteschlange liest dann niemand")

    async def test_a_running_agent_is_left_alone(self):
        """Wecken kostet einen Containerstart — nicht bei jedem Aufruf."""
        agent = SimpleNamespace(id="a1", container_id="c1")
        orig = _patch(agent, "running")
        try:
            ok = await agent_wakeup.ensure_agent_running("a1", orig[2], redis=None)
        finally:
            _restore(orig[0], orig[1])
        self.assertTrue(ok)
        self.assertEqual(_Manager.started, [])

    async def test_an_unknown_agent_is_no_crash(self):
        orig = _patch(None, "exited")
        try:
            ok = await agent_wakeup.ensure_agent_running("weg", orig[2], redis=None)
        finally:
            _restore(orig[0], orig[1])
        self.assertFalse(ok)

    async def test_a_failed_start_does_not_break_delivery(self):
        """Die Nachricht soll trotzdem in die Warteschlange — sie wird beim
        naechsten Start gelesen. Nur eine Antwort binnen Frist gibt es nicht."""
        class _Boom(_Manager):
            async def start_agent(self, agent_id):
                raise RuntimeError("Docker weg")

        import app.core.agent_manager as am_mod

        agent = SimpleNamespace(id="a1", container_id="c1")
        orig = _patch(agent, "exited")
        am_mod.AgentManager = _Boom
        try:
            ok = await agent_wakeup.ensure_agent_running("a1", orig[2], redis=None)
        finally:
            _restore(orig[0], orig[1])
        self.assertFalse(ok)  # ehrlich: er laeuft nicht


class OrderOfOperationsTests(unittest.IsolatedAsyncioTestCase):
    """Wecken muss VOR dem Einreihen geschehen."""

    async def test_wake_happens_before_the_queue_push(self):
        from app.api import agents as api

        order: list[str] = []

        async def _fake_wake(agent_id, docker, redis):
            order.append("wecken")
            return True

        class _RedisClient:
            async def hgetall(self, _key):
                return {}

            async def lpush(self, _key, _payload):
                order.append("einreihen")

            async def publish(self, *_a):
                pass

            async def incr(self, _k):
                return 1

            async def expire(self, *_a):
                pass

        class _Redis:
            client = _RedisClient()

        class _Db:
            def add(self, _obj):
                pass

            async def commit(self):
                pass

        class _Mgr:
            docker = object()

            async def _get_agent(self, _id):
                return SimpleNamespace(id="a2", container_id="c2")

        import app.core.agent_wakeup as wake_mod

        orig_wake = wake_mod.ensure_agent_running
        wake_mod.ensure_agent_running = _fake_wake
        try:
            await api.send_message_to_agent(
                agent_id="a2",
                body=SimpleNamespace(text="Hallo Welt", from_agent_id="lead",
                                     from_name="Lead", message_type="message",
                                     reply_to=None),
                # Agenten-Token: der Besitzer-Check entfaellt, wie bei jeder
                # Nachricht zwischen zwei Agenten.
                user=SimpleNamespace(id="lead", principal_type="agent"),
                db=_Db(),
                manager=_Mgr(),
                redis=_Redis(),
            )
        finally:
            wake_mod.ensure_agent_running = orig_wake

        self.assertEqual(order, ["wecken", "einreihen"],
                         "Erst wecken, dann zustellen — andersherum liest die "
                         "Nachricht im Zweifel niemand")


if __name__ == "__main__":
    unittest.main()
