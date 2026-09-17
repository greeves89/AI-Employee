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
    """Ein Container, der nach dem Start WIRKLICH laeuft (``after_start``) —
    oder eben nicht (#774: Quota-Stopp, Startfehler). Das Double muss beides
    koennen, sonst beweist der Test nur, dass ``start_agent`` gerufen wurde."""

    def __init__(self, status: str, after_start: str = "running"):
        self._status = status
        self._after_start = after_start
        self.asked: list[str] = []

    def get_container_status(self, container_id: str) -> str:
        self.asked.append(container_id)
        return self._status

    def started(self) -> None:
        self._status = self._after_start


class _Manager:
    """Statt eines echten AgentManager — merkt sich, wer gestartet wurde, und
    laesst den Container-Double in seinen Nach-Start-Zustand wechseln."""

    started: list[str] = []

    def __init__(self, db, docker, redis):
        self._docker = docker

    async def start_agent(self, agent_id: str):
        _Manager.started.append(agent_id)
        self._docker.started()
        return SimpleNamespace(id=agent_id, container_id="c1")


class _Session:
    def __init__(self, agent):
        self._agent = agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalar(self, _query):
        return self._agent


def _patch(monkey_agent, docker_status: str, after_start: str = "running"):
    """Haengt die Fremdteile (DB, AgentManager) an Fakes."""
    import app.core.agent_manager as am_mod
    import app.db.session as sess_mod

    _Manager.started = []
    orig_mgr = am_mod.AgentManager
    orig_factory = sess_mod.async_session_factory
    am_mod.AgentManager = _Manager
    sess_mod.async_session_factory = lambda: _Session(monkey_agent)
    return orig_mgr, orig_factory, _Docker(docker_status, after_start)


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

    async def test_a_start_that_leaves_the_container_stopped_is_reported_as_not_running(self):
        """#774: ``start_agent`` kam ohne Fehler zurueck, der Container steht
        trotzdem (Quota-Stopp des disk_monitor, #714; oder er kam nie hoch).
        Die alte Fassung gab hier True zurueck — der Aufrufer stellte zu und
        glaubte, jemand liest mit."""
        agent = SimpleNamespace(id="a1", container_id="c1")
        orig = _patch(agent, "exited", after_start="exited")
        try:
            ok = await agent_wakeup.ensure_agent_running("a1", orig[2], redis=None)
        finally:
            _restore(orig[0], orig[1])
        self.assertEqual(_Manager.started, ["a1"])  # geweckt wurde durchaus
        self.assertFalse(ok, "Der Container steht nach dem Start — 'laeuft' waere gelogen")
        # Nachgemessen, nicht angenommen: nach dem Start wurde der Status erneut abgefragt.
        self.assertEqual(orig[2].asked, ["c1", "c1"])

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


class HonestDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """#774: Scheitert das Wecken, wird trotzdem eingereiht — aber der
    Absender erfaehrt es, statt ein "sent" zu bekommen und 45 s auf eine
    Antwort zu warten, die nicht kommen kann."""

    async def _send(self, wake_result: bool):
        from app.api import agents as api

        order: list[str] = []

        async def _fake_wake(agent_id, docker, redis):
            order.append("wecken")
            return wake_result

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
            result = await api.send_message_to_agent(
                agent_id="a2",
                body=SimpleNamespace(text="Hallo Welt", from_agent_id="lead",
                                     from_name="Lead", message_type="message",
                                     reply_to=None),
                user=SimpleNamespace(id="lead", principal_type="agent"),
                db=_Db(),
                manager=_Mgr(),
                redis=_Redis(),
            )
        finally:
            wake_mod.ensure_agent_running = orig_wake
        return result, order

    async def test_wake_failure_is_visible_to_the_sender_but_the_message_is_still_queued(self):
        result, order = await self._send(wake_result=False)
        self.assertIn("einreihen", order, "Die Nachricht gehoert trotzdem in die Warteschlange")
        self.assertIs(result["target_running"], False,
                      "Der Absender muss erfahren, dass niemand zuhoert")
        # Nicht als 'busy' verkleidet — das waere die falsche Erklaerung.
        self.assertFalse(result["will_reply_later"])

    async def test_a_running_target_is_reported_as_running(self):
        result, _order = await self._send(wake_result=True)
        self.assertIs(result["target_running"], True)


class MeetingWakeTests(unittest.IsolatedAsyncioTestCase):
    """#774: der Besprechungs-Wrapper verschluckt den Rueckgabewert nicht mehr."""

    async def test_wrapper_passes_the_result_through(self):
        from app.api import meeting_rooms as mr
        import app.core.agent_wakeup as wake_mod

        async def _asleep(agent_id, docker, redis):
            return False

        async def _awake(agent_id, docker, redis):
            return True

        orig_wake = wake_mod.ensure_agent_running
        try:
            wake_mod.ensure_agent_running = _asleep
            self.assertIs(await mr._ensure_agent_running("a1", object(), None), False)
            wake_mod.ensure_agent_running = _awake
            self.assertIs(await mr._ensure_agent_running("a1", object(), None), True)
        finally:
            wake_mod.ensure_agent_running = orig_wake


if __name__ == "__main__":
    unittest.main()
