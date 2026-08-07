"""Tests fuer die Microsoft-SSO-Durchreichung beim MCP-Login (OpenWebUI).

Bisher verlangte ``/api/v1/oauth/authorize`` eine bestehende AI-Employee-Sitzung und
schickte sonst auf die Anmeldemaske der Plattform. Fuer einen Server, der nichts
anderes als Microsoft-Daten herausgibt, ist das Microsoft-Konto der Ausweis — also
geht es direkt zur Entra-Anmeldung und danach zurueck zur offenen Freigabe.

Geprueft wird das Sicherheitsnetz drumherum: das Rueckkehrziel darf nicht nach
draussen zeigen, es reist serverseitig im State mit, und die Schleife hat einen
Anschlag.
"""

import asyncio
import json
import unittest

from app.api.auth import safe_internal_path


def _run(coro):
    return asyncio.run(coro)


class SafeReturnTargetTests(unittest.TestCase):
    """Das Rueckkehrziel ist ein Redirect — also die klassische Open-Redirect-Flaeche."""

    def test_accepts_internal_paths(self):
        for path in (
            "/dashboard",
            "/api/v1/oauth/authorize?client_id=mcp_x&state=abc",
            "/integrations",
        ):
            self.assertEqual(safe_internal_path(path), path)

    def test_rejects_anything_pointing_outward(self):
        for path in (
            "//evil.com",                      # protokoll-relativ
            "/\\evil.com",                     # Backslash-Variante
            "https://evil.com/steal",          # absolute URL
            "http://localhost:8000/dashboard",
            "evil.com",                        # ohne fuehrenden Slash
            "",
            None,
            " ",
            "/" + "a" * 2100,                  # Laengenanschlag
        ):
            self.assertEqual(safe_internal_path(path), "", f"durchgelassen: {path!r}")


class _FakeRedisClient:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()


class StateCarriesReturnTargetTests(unittest.TestCase):
    """Das Ziel reist im serverseitigen State — nie als Parameter, den der Client setzt."""

    def _service(self):
        from app.services.sso_service import SSOService
        return SSOService(db=None, redis=_FakeRedis())

    def test_login_url_stores_return_target_server_side(self):
        from unittest.mock import patch

        from app.config import settings

        svc = self._service()
        target = "/api/v1/oauth/authorize?client_id=mcp_abc"
        with patch.object(settings, "oauth_microsoft_client_id", "cid"), \
             patch.object(settings, "oauth_microsoft_client_secret", "secret"):
            url = _run(svc.generate_login_url("microsoft", return_to=target))

        # Das Ziel taucht NICHT in der URL zum Identitaetsanbieter auf ...
        self.assertNotIn("oauth/authorize", url.split("state=")[0])
        # ... sondern liegt unter dem State-Schluessel bei uns.
        (record,) = list(svc.redis.client.store.values())
        self.assertEqual(json.loads(record), {"provider": "microsoft", "return_to": target})

    def test_callback_state_without_return_target_still_works(self):
        """Alte State-Eintraege (nur der Anbietername) brechen einen laufenden Login nicht."""
        from unittest.mock import patch

        svc = self._service()
        _run(svc.redis.client.setex("sso:state:plain", 600, "microsoft"))

        async def _fail(*a, **kw):
            raise AssertionError("darf nach dem State-Check nicht mehr weiterlaufen")

        with patch.object(svc, "_exchange_code", _fail):
            with self.assertRaises(AssertionError):
                _run(svc.handle_callback("microsoft", "code", "plain"))
        # Der State wurde als gueltig akzeptiert und verbraucht (Einmal-Nutzung).
        self.assertNotIn("sso:state:plain", svc.redis.client.store)

    def test_callback_rejects_state_of_another_provider(self):
        svc = self._service()
        _run(svc.redis.client.setex(
            "sso:state:x", 600, json.dumps({"provider": "google", "return_to": "/dashboard"})
        ))
        with self.assertRaises(ValueError):
            _run(svc.handle_callback("microsoft", "code", "x"))


class AuthorizeFlowSourceTests(unittest.TestCase):
    """Der Ablauf selbst — als Quelltext-Pruefung, weil der Endpunkt die halbe
    API-Schicht (inkl. Docker-Abhaengigkeit) mitzieht."""

    @staticmethod
    def _source(rel: str) -> str:
        from pathlib import Path
        return (Path(__file__).resolve().parents[1] / rel).read_text()

    def test_authorize_sends_user_to_microsoft_not_to_the_platform_login(self):
        src = self._source("app/api/oauth_as.py")
        self.assertIn("/api/v1/auth/sso/microsoft/login?redirect=", src)
        # Die Plattform-Anmeldung bleibt als Rueckfallebene, wenn kein SSO steht.
        self.assertIn("/login?redirect=", src)

    def test_loop_guard_exists(self):
        """Nach einem SSO-Versuch ohne nutzbare Sitzung wird erklaert, nicht geschleift."""
        src = self._source("app/api/oauth_as.py")
        self.assertIn('_SSO_DONE = "sso_done"', src)
        self.assertIn('q.get(_SSO_DONE) != "1"', src)
        self.assertIn('q.get(_SSO_DONE) == "1"', src)

    def test_remembered_consent_is_scoped_and_revocable(self):
        core = self._source("app/core/mcp_oauth.py")
        self.assertIn("async def remember_grant", core)
        self.assertIn("async def has_grant", core)
        self.assertIn("async def forget_grants", core)
        # Zustimmung wird pro (User, Client) gemerkt — nicht global.
        self.assertIn('f"{_GRANT_PREFIX}{user_id}:{client_id}"', core)
        # Und beim Trennen der Microsoft-Verbindung wieder eingesammelt.
        self.assertIn("forget_grants", self._source("app/services/oauth_service.py"))


if __name__ == "__main__":
    unittest.main()
