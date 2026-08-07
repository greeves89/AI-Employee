"""Die "Meldebremse": ein proaktiver Agent ohne Arbeit meldet sich hoechstens
einmal pro Halbtag.

Vorfall 2026-08-06 (HANDOVER.md): Neun proaktive Agenten, die sich alle beim
Leerlauf melden, sind Dauerbeschuss — der Nutzer schaltet sie nach drei Tagen ab.
Der PROACTIVE_PROMPT bittet den Agenten, sich selbst zu bremsen (STEP 3), aber
Prompt-Text allein ist keine Durchsetzung (vgl. [[autonomy-matrix-enforcement]]-
Lektion: Vorgabe im Prompt vs. Erzwingung im Code sind zwei verschiedene Dinge).
Diese Tests pruefen die serverseitige Bremse in notifications.py.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from fastapi import Response

from app.api.notifications import CHECKIN_COOLDOWN_SECONDS, NotificationCreate, _checkin_allowed, create_notification


class FakeRedisClient:
    """Minimal stand-in for redis.asyncio.Redis.set(nx=, ex=) semantics."""

    def __init__(self, raise_on_call: bool = False):
        self._store: dict[str, str] = {}
        self._raise = raise_on_call

    async def set(self, key, value, nx=False, ex=None):
        if self._raise:
            raise ConnectionError("redis unreachable")
        if nx and key in self._store:
            return None  # redis-py returns None/False when NX prevents the write
        self._store[key] = value
        return True

    async def publish(self, channel, message):
        return 0


class FakeRedisService:
    def __init__(self, client):
        self.client = client


class CheckinAllowedTests(unittest.TestCase):
    def test_first_checkin_in_window_is_allowed(self):
        redis = FakeRedisService(FakeRedisClient())
        self.assertTrue(asyncio.run(_checkin_allowed(redis, "agent-1")))

    def test_second_checkin_in_the_same_window_is_denied(self):
        redis = FakeRedisService(FakeRedisClient())
        self.assertTrue(asyncio.run(_checkin_allowed(redis, "agent-1")))
        self.assertFalse(asyncio.run(_checkin_allowed(redis, "agent-1")))

    def test_different_agents_do_not_share_a_cooldown(self):
        redis = FakeRedisService(FakeRedisClient())
        self.assertTrue(asyncio.run(_checkin_allowed(redis, "agent-1")))
        self.assertTrue(asyncio.run(_checkin_allowed(redis, "agent-2")))

    def test_cooldown_window_is_twelve_hours(self):
        self.assertEqual(CHECKIN_COOLDOWN_SECONDS, 12 * 60 * 60)

    def test_no_redis_client_fails_open(self):
        """Kein Redis verbunden (z.B. frueh im Start) darf eine echte Meldung nie schlucken."""
        redis = FakeRedisService(None)
        self.assertTrue(asyncio.run(_checkin_allowed(redis, "agent-1")))

    def test_redis_error_fails_open(self):
        """Eine verpasste Bremse ist billiger als eine verschluckte echte Meldung."""
        redis = FakeRedisService(FakeRedisClient(raise_on_call=True))
        self.assertTrue(asyncio.run(_checkin_allowed(redis, "agent-1")))


def _make_db_mock():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


class CreateNotificationCooldownTests(unittest.TestCase):
    """Direkter Aufruf des Endpunkt-Handlers (kein HTTP) — dieselbe Funktion, die
    notify_user(is_checkin=true) am Ende erreicht."""

    def test_second_checkin_is_suppressed_and_never_touches_the_db(self):
        redis = FakeRedisService(FakeRedisClient())
        body = NotificationCreate(
            agent_id="agent-1", title="Nichts zu tun", message="",
            priority="normal", meta={"is_checkin": True},
        )
        db1, db2 = _make_db_mock(), _make_db_mock()
        resp1, resp2 = Response(), Response()
        first = asyncio.run(create_notification(body, resp1, db=db1, redis=redis, _auth={"agent_id": "agent-1"}))
        self.assertNotIn("suppressed", first)
        db1.add.assert_called_once()

        second = asyncio.run(create_notification(body, resp2, db=db2, redis=redis, _auth={"agent_id": "agent-1"}))
        self.assertTrue(second.get("suppressed"))
        db2.add.assert_not_called()
        self.assertEqual(resp2.status_code, 200, "suppressed = nothing created, 201 would be misleading")

    def test_non_checkin_notifications_are_never_rate_limited(self):
        """Echte Ergebnisse (STEP 6, Fehlermeldungen) duerfen beliebig oft raus."""
        redis = FakeRedisService(FakeRedisClient())
        body = NotificationCreate(
            agent_id="agent-1", title="TODO erledigt", message="x",
            priority="normal", meta={},
        )
        for _ in range(3):
            db = _make_db_mock()
            result = asyncio.run(create_notification(body, Response(), db=db, redis=redis, _auth={"agent_id": "agent-1"}))
            self.assertNotIn("suppressed", result)
            db.add.assert_called_once()

    def test_urgent_checkin_bypasses_the_cooldown(self):
        """Der Sicherheitsventil aus ERROR HANDLING: 'high'/'urgent' geht immer durch,
        auch wenn faelschlich als is_checkin markiert."""
        redis = FakeRedisService(FakeRedisClient())
        body = NotificationCreate(
            agent_id="agent-1", title="Dringend", message="x",
            priority="urgent", meta={"is_checkin": True},
        )
        for _ in range(2):
            db = _make_db_mock()
            result = asyncio.run(create_notification(body, Response(), db=db, redis=redis, _auth={"agent_id": "agent-1"}))
            self.assertNotIn("suppressed", result)
            db.add.assert_called_once()


class AgentIdSpoofingTests(unittest.TestCase):
    """verify_agent_token only checks the token against WHATEVER agent_id it is
    given (header, else this same body field) — it never cross-checks that value
    against the body once authenticated. Without an override, an agent holding
    its own valid token could put a DIFFERENT agent_id in the body and create
    notifications — and poison another agent's check-in cooldown — as that
    other agent. create_notification must use the authenticated identity, not
    the client-supplied one."""

    def test_notification_is_created_under_the_authenticated_agent_not_the_spoofed_one(self):
        redis = FakeRedisService(FakeRedisClient())
        body = NotificationCreate(
            agent_id="victim-agent", title="x", message="", priority="normal", meta={},
        )
        db = _make_db_mock()
        asyncio.run(create_notification(body, Response(), db=db, redis=redis, _auth={"agent_id": "attacker-agent"}))
        created = db.add.call_args[0][0]
        self.assertEqual(created.agent_id, "attacker-agent")
        self.assertEqual(body.agent_id, "attacker-agent", "body.agent_id must be overridden, not merely ignored")

    def test_spoofed_body_agent_id_cannot_poison_another_agents_cooldown(self):
        redis = FakeRedisService(FakeRedisClient())
        spoofed_body = NotificationCreate(
            agent_id="victim-agent", title="x", message="", priority="normal",
            meta={"is_checkin": True},
        )
        asyncio.run(create_notification(
            spoofed_body, Response(), db=_make_db_mock(), redis=redis,
            _auth={"agent_id": "attacker-agent"},
        ))
        # The victim's own, honestly-authenticated check-in must still go through.
        victim_body = NotificationCreate(
            agent_id="victim-agent", title="y", message="", priority="normal",
            meta={"is_checkin": True},
        )
        victim_db = _make_db_mock()
        result = asyncio.run(create_notification(
            victim_body, Response(), db=victim_db, redis=redis,
            _auth={"agent_id": "victim-agent"},
        ))
        self.assertNotIn("suppressed", result)
        victim_db.add.assert_called_once()


if __name__ == "__main__":
    unittest.main()
