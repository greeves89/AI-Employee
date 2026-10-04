"""Leerlauf-Sweep und Kanal-Nachrichten.

Beobachtet auf einer Raspberry-Pi-Installation (2026-09-26): Wer nur per
Telegram mit seinen Agenten sprach, bekam "Agent hochgefahren!" — und der
Agent war kurz darauf wieder aus. Im Log wechselten sich
"[UserLifecycle] Woke agent" und "Stopped agent ... after 120min inactivity"
im Minutentakt ab. Ursache: Der Sweep misst Aktivitaet nur an
``user.last_active_at``, und das setzt ausschliesslich die Web-Oberflaeche.

Seit v1.339.4 schuetzt _frisch_gestartet einen gerade geweckten Agenten —
aber nur ab Containerstart. Laeuft der Agent schon laenger als die Frist,
wird er nach seiner Antwort beim naechsten Sweep gestoppt, und jede
Folgenachricht wartet wieder aufs Hochfahren. Diese Tests laufen deshalb
bewusst mit einem Container, der seit Stunden laeuft.

Zweitens schaltete "Auto-Stop Idle Agents = 0" in der Admin-Konsole diesen
Sweep nicht ab: Die Konsole schreibt ``max_idle_minutes``, der Sweep las nur
``agent_idle_timeout_minutes`` und fiel auf 30 Minuten zurueck.
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core import channel_gateway as gw
from app.models.agent import AgentState
from app.services import user_lifecycle as ul


class _FakeRedisClient:
    def __init__(self):
        self.keys: dict = {}
        self.lists: dict = {}

    async def set(self, key, value, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def get(self, key):
        return self.keys.get(key)

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()
        self.get_queue_depth = AsyncMock(return_value=0)
        self.get_agent_status = AsyncMock(return_value={})


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    """Liefert beim ersten execute() die Agenten, beim zweiten die Nutzer —
    genau die Reihenfolge in ``_sweep``."""

    def __init__(self, agents, users):
        self._answers = [_Result(agents), _Result(users)]
        self.commit = AsyncMock()

    async def execute(self, _stmt):
        return self._answers.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


NOW = datetime.now(timezone.utc)
LONG_AGO = NOW - timedelta(hours=5)


def _agent(**over):
    data = dict(id="a1", name="CEO", user_id="u1", config={}, container_id="c1",
                state=AgentState.RUNNING)
    data.update(over)
    return SimpleNamespace(**data)


def _user(last_active_at=LONG_AGO):
    return SimpleNamespace(id="u1", email="nutzer@example.org", last_active_at=last_active_at)


def _service(redis):
    svc = ul.UserLifecycleService.__new__(ul.UserLifecycleService)
    svc.db_factory = MagicMock()
    svc.docker = MagicMock()
    # Container laeuft seit Stunden — sonst griffe schon _frisch_gestartet
    # (Docker-Format mit Nanosekunden, wie im Betrieb).
    svc.docker.get_container.return_value.attrs = {
        "State": {"StartedAt": LONG_AGO.strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"}}
    svc.redis = redis
    return svc


async def _run_sweep(svc, agent, user, timeout=30):
    db = _FakeDb([agent], [user])
    with patch("app.db.session.resilient_session", lambda **_kw: db), \
         patch.object(ul, "_get_timeout_minutes", AsyncMock(return_value=timeout)), \
         patch.object(ul, "_has_imminent_schedule", AsyncMock(return_value=False)):
        await svc._sweep()


class InteractionMarkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip(self):
        redis = _FakeRedis()
        self.assertIsNone(await ul._last_interaction(redis, "a1"))
        await ul.mark_agent_interaction(redis, "a1", now=NOW)
        got = await ul._last_interaction(redis, "a1")
        self.assertAlmostEqual(got.timestamp(), NOW.timestamp(), places=3)

    async def test_is_per_agent(self):
        redis = _FakeRedis()
        await ul.mark_agent_interaction(redis, "a1", now=NOW)
        self.assertIsNone(await ul._last_interaction(redis, "a2"))

    async def test_redis_failure_never_raises(self):
        """Die Zustellung darf an der Markierung nie scheitern."""
        broken = SimpleNamespace(client=SimpleNamespace(
            set=AsyncMock(side_effect=ConnectionError("weg")),
            get=AsyncMock(side_effect=ConnectionError("weg")),
        ))
        await ul.mark_agent_interaction(broken, "a1")
        self.assertIsNone(await ul._last_interaction(broken, "a1"))


class GatewayMarksInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivered_message_counts_as_activity(self):
        redis = _FakeRedis()
        msg = gw.InboundMessage(agent_id="a1", text="Hallo", channel=gw.CHANNEL_TELEGRAM,
                                conversation_id="c1", message_id="m1", context={"chat_id": "c1"})
        with patch.object(gw, "persist_message", AsyncMock()), \
             patch.object(gw, "capture_if_worthwhile", AsyncMock()):
            self.assertTrue(await gw.deliver(redis, msg))
        self.assertIsNotNone(await ul._last_interaction(redis, "a1"))

    async def test_duplicate_is_not_marked_again(self):
        """Eine verworfene Doppel-Zustellung ist kein Gespraech."""
        redis = _FakeRedis()
        msg = gw.InboundMessage(agent_id="a1", text="Hallo", channel=gw.CHANNEL_TELEGRAM,
                                conversation_id="c1", message_id="m1", context={"chat_id": "c1"})
        await gw.already_seen(redis, msg)
        with patch.object(gw, "persist_message", AsyncMock()), \
             patch.object(gw, "capture_if_worthwhile", AsyncMock()):
            self.assertFalse(await gw.deliver(redis, msg))
        self.assertIsNone(await ul._last_interaction(redis, "a1"))


class EveryTelegramPathMarksTests(unittest.TestCase):
    """Text geht durch channel_gateway.deliver, Fotos/Sprachnachrichten/Dokumente
    nicht — sie reiht agent_bot._handle_media selbst ein. Der gemeldete Fall
    war genau ein Screenshot an den CEO-Agenten; ein Fix nur im Gateway haette
    ihn nicht behoben."""

    def test_media_path_marks_interaction(self):
        src = (Path(__file__).resolve().parents[1] / "app" / "telegram" / "agent_bot.py").read_text()
        start = src.index("async def _handle_media")
        end = src.index("async def _handle_callback", start)
        media = src[start:end]
        # Eingereiht wird seit #898 ueber chat_auftrag.einreihen (Budgetpruefung).
        self.assertIn("einreihen(redis, target_agent_id", media)
        self.assertIn("mark_agent_interaction(", media)


class SweepHonoursChannelActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_telegram_message_keeps_agent_awake(self):
        """Web-Oberflaeche seit Stunden zu, Agent laeuft seit Stunden, gerade
        per Telegram geschrieben — der Agent muss wach bleiben."""
        redis = _FakeRedis()
        await ul.mark_agent_interaction(redis, "a1", now=NOW - timedelta(seconds=20))
        svc, agent = _service(redis), _agent()
        await _run_sweep(svc, agent, _user())
        svc.docker.stop_container.assert_not_called()
        self.assertEqual(agent.state, AgentState.RUNNING)

    async def test_old_telegram_message_does_not_keep_agent_forever(self):
        redis = _FakeRedis()
        await ul.mark_agent_interaction(redis, "a1", now=LONG_AGO)
        svc, agent = _service(redis), _agent()
        await _run_sweep(svc, agent, _user())
        svc.docker.stop_container.assert_called_once_with("c1")
        self.assertEqual(agent.state, AgentState.STOPPED)

    async def test_without_any_activity_agent_is_still_stopped(self):
        """Das bisherige Verhalten bleibt: niemand da, Agent schlaeft."""
        svc, agent = _service(_FakeRedis()), _agent()
        await _run_sweep(svc, agent, _user(last_active_at=None))
        svc.docker.stop_container.assert_called_once_with("c1")

    async def test_activity_on_another_agent_does_not_count(self):
        redis = _FakeRedis()
        await ul.mark_agent_interaction(redis, "a2", now=NOW)
        svc, agent = _service(redis), _agent()
        await _run_sweep(svc, agent, _user())
        svc.docker.stop_container.assert_called_once_with("c1")


class TimeoutSourceTests(unittest.IsolatedAsyncioTestCase):
    async def _timeout(self, api_value, console_value):
        db = MagicMock()
        db.get = AsyncMock(return_value=(
            None if console_value is None else SimpleNamespace(value=console_value)))
        svc = MagicMock()
        svc.get = AsyncMock(return_value=api_value)
        with patch("app.services.settings_service.SettingsService", return_value=svc):
            return await ul._get_timeout_minutes(db)

    async def test_console_zero_disables_the_sweep(self):
        """"Auto-Stop Idle Agents = 0" muss auch diesen Sweep abschalten."""
        self.assertEqual(await self._timeout(None, "0"), 0)

    async def test_console_value_is_used(self):
        self.assertEqual(await self._timeout(None, "45"), 45)

    async def test_explicit_api_setting_wins(self):
        self.assertEqual(await self._timeout("10", "0"), 10)

    async def test_default_without_any_setting(self):
        self.assertEqual(await self._timeout(None, None), ul.DEFAULT_INACTIVITY_MINUTES)


if __name__ == "__main__":
    unittest.main()
