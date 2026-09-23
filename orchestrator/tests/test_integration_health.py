"""Tests fuer den Token-Zustand verbundener Integrationen (app/core/integration_health.py).

Anlass: der Anthropic-Refresh-Token lief ab, die Integrationsseite zeigte trotzdem
elf Tage lang "Connected", und niemand wurde benachrichtigt. Diese Tests halten
fest, dass (a) ein abgelaufener Token als ``expired`` erscheint, (b) ein
scheiternder Refresh als ``refresh_failing`` vorwarnt, (c) nur endgueltige Fehler
melden und (d) das hoechstens einmal am Tag.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core import integration_health as ih

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class FakeRedisClient:
    """Genug Redis fuer get/set(nx, ex)/delete — Ablauf wird hier nicht simuliert."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex:
            self.ttl[key] = ex
        return True

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
            self.ttl.pop(k, None)


def _redis():
    return SimpleNamespace(client=FakeRedisClient())


class StatusTests(unittest.TestCase):
    def test_not_connected(self):
        self.assertEqual(ih.integration_status(False, None, None, NOW), ih.STATUS_DISCONNECTED)

    def test_valid_token(self):
        self.assertEqual(
            ih.integration_status(True, NOW + timedelta(hours=2), None, NOW), ih.STATUS_CONNECTED
        )

    def test_expired_token_wins_over_everything(self):
        # Genau der Vorfall: expires_at vorbei, Karte zeigte trotzdem "Connected".
        self.assertEqual(
            ih.integration_status(True, NOW - timedelta(days=11), None, NOW), ih.STATUS_EXPIRED
        )
        self.assertEqual(
            ih.integration_status(True, NOW - timedelta(seconds=1), {"error": "x"}, NOW),
            ih.STATUS_EXPIRED,
        )

    def test_failing_refresh_warns_before_expiry(self):
        self.assertEqual(
            ih.integration_status(True, NOW + timedelta(minutes=8), {"error": "HTTP 400"}, NOW),
            ih.STATUS_REFRESH_FAILING,
        )

    def test_no_expiry_means_nothing_to_refresh(self):
        # GitHub-PAT und Co.: kein expires_at, also immer "connected", solange verbunden.
        self.assertEqual(ih.integration_status(True, None, None, NOW), ih.STATUS_CONNECTED)

    def test_naive_expiry_is_treated_as_utc(self):
        naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
        self.assertEqual(ih.integration_status(True, naive, None, NOW), ih.STATUS_EXPIRED)


class DescribeErrorTests(unittest.TestCase):
    def test_oauth_error_body(self):
        body = json.dumps({"error": "invalid_grant", "error_description": "Refresh token expired"})
        code, text = ih.describe_refresh_error(400, body)
        self.assertEqual(code, "invalid_grant")
        self.assertEqual(text, "HTTP 400 – invalid_grant: Refresh token expired")

    def test_non_json_body_is_not_echoed(self):
        # Unbekannter Anbieterinhalt landet nie ungefiltert in UI oder Meldung.
        code, text = ih.describe_refresh_error(502, "<html>secret-ish gateway page</html>")
        self.assertIsNone(code)
        self.assertEqual(text, "HTTP 502")

    def test_permanent_vs_transient(self):
        self.assertTrue(ih.is_permanent_failure(400, "invalid_grant"))
        self.assertTrue(ih.is_permanent_failure(401, "invalid_client"))
        self.assertTrue(ih.is_permanent_failure(401, None))
        self.assertFalse(ih.is_permanent_failure(503, None))
        self.assertFalse(ih.is_permanent_failure(429, None))
        self.assertFalse(ih.is_permanent_failure(400, "temporarily_unavailable"))


class FailureStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_permanent_failure_alerts_once_per_day(self):
        redis = _redis()
        first = await ih.record_refresh_failure(redis, 4, "HTTP 400 – invalid_grant", True, NOW)
        second = await ih.record_refresh_failure(redis, 4, "HTTP 400 – invalid_grant", True, NOW)
        self.assertTrue(first)
        self.assertFalse(second)  # der Hintergrund-Refresh laeuft alle 5 min — kein Alarmsturm
        self.assertEqual(redis.client.ttl["oauth:refresh_alert:4"], ih.ALERT_REPEAT_SECONDS)
        # Der Fehler selbst verfaellt, damit keine Reste geloeschter Integrationen bleiben.
        self.assertEqual(redis.client.ttl["oauth:refresh_failure:4"], ih.FAILURE_TTL_SECONDS)
        failure = await ih.get_refresh_failure(redis, 4)
        self.assertEqual(failure["error"], "HTTP 400 – invalid_grant")

    async def test_transient_failure_is_recorded_but_silent(self):
        redis = _redis()
        self.assertFalse(await ih.record_refresh_failure(redis, 4, "HTTP 503", False, NOW))
        self.assertIsNotNone(await ih.get_refresh_failure(redis, 4))
        self.assertNotIn("oauth:refresh_alert:4", redis.client.store)

    async def test_clear_resets_failure_and_alert_lock(self):
        # Nach einem Neu-Login muss ein Rueckfall am selben Tag wieder melden.
        redis = _redis()
        await ih.record_refresh_failure(redis, 4, "HTTP 400", True, NOW)
        await ih.clear_refresh_failure(redis, 4)
        self.assertIsNone(await ih.get_refresh_failure(redis, 4))
        self.assertTrue(await ih.record_refresh_failure(redis, 4, "HTTP 400", True, NOW))

    async def test_without_redis_status_falls_back_and_permanent_still_alerts(self):
        no_redis = SimpleNamespace(client=None)
        self.assertIsNone(await ih.get_refresh_failure(no_redis, 4))
        self.assertTrue(await ih.record_refresh_failure(no_redis, 4, "HTTP 400", True, NOW))
        self.assertFalse(await ih.record_refresh_failure(no_redis, 4, "HTTP 503", False, NOW))


class OAuthServiceReportTests(unittest.IsolatedAsyncioTestCase):
    """Der Weg von einer gescheiterten Token-Antwort bis zur Meldung."""

    def _service(self):
        from app.services.oauth_service import OAuthService

        svc = OAuthService(db=AsyncMock(), redis=_redis())
        svc._alert_refresh_failure = AsyncMock()
        return svc

    def _integration(self):
        return SimpleNamespace(id=4, provider=SimpleNamespace(value="anthropic"), user_id=None)

    async def test_invalid_grant_alerts_once(self):
        svc = self._service()
        body = json.dumps({"error": "invalid_grant", "error_description": "Refresh token expired"})
        await svc._report_refresh_failure(self._integration(), 400, body)
        await svc._report_refresh_failure(self._integration(), 400, body)
        svc._alert_refresh_failure.assert_awaited_once()
        args = svc._alert_refresh_failure.await_args.args
        self.assertIn("invalid_grant", args[1])

    async def test_server_error_does_not_alert(self):
        svc = self._service()
        await svc._report_refresh_failure(self._integration(), 503, "")
        svc._alert_refresh_failure.assert_not_awaited()
        self.assertIsNotNone(await ih.get_refresh_failure(svc.redis, 4))

    async def test_report_never_raises(self):
        svc = self._service()
        svc._alert_refresh_failure = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(ih, "record_refresh_failure", AsyncMock(return_value=True)):
            await svc._report_refresh_failure(self._integration(), 400, "{}")  # kein Raise


if __name__ == "__main__":
    unittest.main()
