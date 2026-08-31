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
        self.assertEqual(json.loads(record), {
            "provider": "microsoft", "return_to": target,
            "base_url": settings.oauth_redirect_base_url,
        })

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


class MultiHostRedirectTests(unittest.TestCase):
    """Ein Kunde mit zwei erreichbaren Hostnamen (eigene Domain + Kurz-Domain) —
    ein SSO-Login muss auf DEMSELBEN Host landen, auf dem er gestartet wurde,
    statt immer fest auf oauth_redirect_base_url zu enden."""

    def _service(self):
        from app.services.sso_service import SSOService
        return SSOService(db=None, redis=_FakeRedis())

    def test_unlisted_host_falls_back_to_the_fixed_base_url(self):
        from unittest.mock import patch
        from app.config import settings
        from app.services.sso_service import resolve_redirect_base_url

        with patch.object(settings, "oauth_redirect_allowed_hosts", "vanity.example.com"):
            self.assertEqual(
                resolve_redirect_base_url("evil.example.com"),
                settings.oauth_redirect_base_url,
            )
            self.assertEqual(resolve_redirect_base_url(None), settings.oauth_redirect_base_url)

    def test_a_listed_host_gets_its_own_https_base_url(self):
        from unittest.mock import patch
        from app.config import settings
        from app.services.sso_service import resolve_redirect_base_url

        with patch.object(settings, "oauth_redirect_allowed_hosts", "vanity.example.com, www.vanity.example.com"):
            self.assertEqual(resolve_redirect_base_url("vanity.example.com"), "https://vanity.example.com")
            self.assertEqual(resolve_redirect_base_url("www.vanity.example.com"), "https://www.vanity.example.com")
            # Port-Anteil im Host-Header bleibt erhalten, Vergleich ist ohne Port.
            self.assertEqual(resolve_redirect_base_url("vanity.example.com:8443"), "https://vanity.example.com:8443")

    def test_empty_allowlist_means_nobody_gets_the_dynamic_path(self):
        """Vorgabe unveraendert: kein Opt-in, kein neues Verhalten."""
        from app.services.sso_service import resolve_redirect_base_url
        from app.config import settings

        self.assertEqual(resolve_redirect_base_url("vanity.example.com"), settings.oauth_redirect_base_url)

    def test_login_and_callback_use_the_same_redirect_uri(self):
        """OAuth2 verlangt identische redirect_uri in Authorize- und Token-Request —
        der Callback muss den im State mitgereisten base_url nehmen, nicht neu raten."""
        from unittest.mock import patch
        from app.config import settings

        svc = self._service()
        seen_redirect_uris = []

        async def _fake_exchange(provider, code, base_url=None):
            seen_redirect_uris.append(f"{base_url}/api/v1/auth/sso/microsoft/callback")
            raise RuntimeError("stop here, we only care about the redirect_uri used")

        with patch.object(settings, "oauth_redirect_allowed_hosts", "vanity.example.com"), \
             patch.object(settings, "oauth_microsoft_client_id", "cid"), \
             patch.object(settings, "oauth_microsoft_client_secret", "secret"):
            url = _run(svc.generate_login_url("microsoft", request_host="vanity.example.com"))
            self.assertIn("redirect_uri=https%3A%2F%2Fvanity.example.com%2Fapi%2Fv1%2Fauth%2Fsso%2Fmicrosoft%2Fcallback", url)

            state = url.split("state=")[1].split("&")[0]
            with patch.object(svc, "_exchange_code", _fake_exchange):
                with self.assertRaises(RuntimeError):
                    _run(svc.handle_callback("microsoft", "code", state))

        self.assertEqual(seen_redirect_uris, ["https://vanity.example.com/api/v1/auth/sso/microsoft/callback"])


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
