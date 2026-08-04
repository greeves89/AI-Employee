"""Computer-Use sessions must survive an orchestrator restart.

Before this, sessions lived only in a process dict: every restart/deploy forced
the user to create a new session and re-enter its id in the bridge app. The
customer's words: "die Session ID ändert sich ja jedes mal ... wenn ComputerUse
Standard werden soll, muss die Session bestehen bleiben".
"""

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.api import computer_use as cu


class _FakeRedisClient:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def scan_iter(self, match=None, count=None):
        for k in list(self.store):
            yield k


def _install_fake_redis():
    client = _FakeRedisClient()
    cu._redis = MagicMock()
    cu._redis.client = client
    return client


def _session(user_id="u1", **over):
    s = {
        "user_id": user_id,
        "created_at": time.time(),
        "last_activity_at": time.time(),
        "bridge_connected": False,
        "bridge_ws": None,
        "action_count": 3,
        "audit_log": [],
        "pending_results": {},
        "allowed_capabilities": {"screenshots", "mouse"},
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "agent_id": None,
        "recording": False,
        "recording_steps": [],
        "capture_human": False,
    }
    s.update(over)
    return s


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cu._sessions.clear()
        self.client = _install_fake_redis()

    async def test_session_is_written_to_redis(self):
        cu._sessions["abc"] = _session()
        await cu._persist_session("abc")
        stored = json.loads(self.client.store[cu._SESSION_KEY + "abc"])
        self.assertEqual(stored["user_id"], "u1")
        self.assertEqual(sorted(stored["allowed_capabilities"]), ["mouse", "screenshots"])

    async def test_survives_a_restart(self):
        """The core scenario: persist, wipe the process dict, look it up again."""
        cu._sessions["abc"] = _session()
        await cu._persist_session("abc")
        cu._sessions.clear()                      # ← "orchestrator restarted"

        restored = await cu._get_session("abc")
        self.assertIsNotNone(restored)
        self.assertEqual(restored["user_id"], "u1")
        self.assertEqual(restored["allowed_capabilities"], {"screenshots", "mouse"})
        # Live-only state must come back clean, not half-restored.
        self.assertFalse(restored["bridge_connected"])
        self.assertIsNone(restored["bridge_ws"])
        self.assertEqual(restored["pending_results"], {})

    async def test_unknown_session_stays_unknown(self):
        self.assertIsNone(await cu._get_session("does-not-exist"))

    async def test_missing_redis_is_not_fatal(self):
        cu._redis = None
        cu._sessions["abc"] = _session()
        await cu._persist_session("abc")          # must not raise
        self.assertIsNotNone(await cu._get_session("abc"))

    async def test_touch_extends_activity(self):
        cu._sessions["abc"] = _session(last_activity_at=time.time() - 500)
        before = cu._sessions["abc"]["last_activity_at"]
        await cu._touch_session("abc")
        self.assertGreater(cu._sessions["abc"]["last_activity_at"], before)


class ReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cu._sessions.clear()
        _install_fake_redis()

    async def test_finds_the_users_own_session(self):
        cu._sessions["mine"] = _session(user_id="u1")
        cu._sessions["theirs"] = _session(user_id="u2")
        found = await cu._find_user_session("u1")
        self.assertEqual(found[0], "mine")

    async def test_prefers_the_one_with_a_live_bridge(self):
        cu._sessions["idle"] = _session(user_id="u1", last_activity_at=time.time())
        cu._sessions["live"] = _session(user_id="u1", bridge_connected=True,
                                        last_activity_at=time.time() - 900)
        found = await cu._find_user_session("u1")
        self.assertEqual(found[0], "live",
                         "reconnecting the tab must not steal the bridge's session")

    async def test_no_session_for_unknown_user(self):
        cu._sessions["mine"] = _session(user_id="u1")
        self.assertIsNone(await cu._find_user_session("nobody"))

    async def test_restores_from_redis_when_process_is_empty(self):
        cu._sessions["abc"] = _session(user_id="u1")
        await cu._persist_session("abc")
        cu._sessions.clear()
        found = await cu._find_user_session("u1")
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "abc")


class IdleTimeoutTests(unittest.TestCase):
    def test_timeout_is_measured_from_activity_not_creation(self):
        """A session in active use must never expire — the old 30min-from-creation
        cap killed live sessions mid-work."""
        old_but_active = _session(
            created_at=time.time() - 11 * 3600,
            last_activity_at=time.time(),
        )
        age = time.time() - float(old_but_active["last_activity_at"])
        self.assertLess(age, cu.SESSION_TIMEOUT_SECS)

    def test_truly_idle_session_does_expire(self):
        idle = _session(last_activity_at=time.time() - (cu.SESSION_TIMEOUT_SECS + 60))
        age = time.time() - float(idle["last_activity_at"])
        self.assertGreater(age, cu.SESSION_TIMEOUT_SECS)


if __name__ == "__main__":
    unittest.main()


class SessionDeletionTests(unittest.IsolatedAsyncioTestCase):
    """Loeschen muss eine Session ENDGUELTIG entfernen.

    Seit der Redis-Persistenz (v1.130.0) raeumte `delete_session` nur den
    Prozess-Speicher. Der Redis-Schluessel blieb liegen, `_restore_session` holte
    die Session beim naechsten Zugriff zurueck, und die Liste scannte sie ohnehin
    wieder ein: fuer den Nutzer war eine Session schlicht nicht loeschbar — nach
    dem Neuladen stand sie wieder da (Kundenmeldung 2026-08-04).
    """

    def setUp(self):
        cu._sessions.clear()
        self.redis = _install_fake_redis()

    async def asyncTearDown(self):
        cu._sessions.clear()

    async def _make(self, sid="sess-1", user_id="u1"):
        cu._sessions[sid] = _session(user_id=user_id)
        await cu._persist_session(sid)
        return sid

    async def test_forget_removes_memory_and_redis(self):
        sid = await self._make()
        self.assertIn(cu._SESSION_KEY + sid, self.redis.store)
        await cu._forget_session(sid)
        self.assertNotIn(sid, cu._sessions)
        self.assertNotIn(cu._SESSION_KEY + sid, self.redis.store)

    async def test_deleted_session_does_not_come_back(self):
        """Der eigentliche Fehler: nach dem Loeschen holte _get_session sie zurueck."""
        sid = await self._make()
        await cu._forget_session(sid)
        self.assertIsNone(await cu._get_session(sid))

    async def test_delete_endpoint_clears_redis(self):
        sid = await self._make(user_id="u1")
        user = SimpleNamespace(id="u1")
        await cu.delete_session(sid, user=user)
        self.assertNotIn(cu._SESSION_KEY + sid, self.redis.store)
        self.assertIsNone(await cu._get_session(sid))

    async def test_delete_leaves_other_sessions_alone(self):
        keep = await self._make("sess-keep")
        drop = await self._make("sess-drop")
        await cu._forget_session(drop)
        self.assertIn(cu._SESSION_KEY + keep, self.redis.store)
        self.assertIsNotNone(await cu._get_session(keep))

    async def test_forget_survives_a_dead_redis(self):
        """Loeschen darf nicht scheitern, nur weil Redis gerade weg ist —
        der Speicher muss trotzdem geraeumt werden."""
        sid = await self._make()
        cu._redis = None
        await cu._forget_session(sid)
        self.assertNotIn(sid, cu._sessions)
