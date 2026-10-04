"""Bridge und Zwei-Faktor (#915-Folge).

1. Die WebSocket-Anmeldung der Bridge nimmt nur echte Zugangstoken an —
   kein Refresh-Token, kein Zwischen-Token der Zwei-Faktor-Anmeldung, keine
   widerrufene Sitzung (token_version), kein deaktiviertes Konto.
2. Verlangt die Anmeldung einen zweiten Faktor, bricht die Bridge nicht mit
   KeyError ab, sondern meldet verständlich, was los ist.
"""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import computer_use
from app.core.auth import create_access_token, create_refresh_token
from app.models.user import User, UserRole

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import tray_app  # noqa: E402


class _Ws:
    headers: dict = {}


class WebsocketNurZugangstoken(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(User.metadata.create_all, tables=[User.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(User(id="u1", email="a@example.invalid", name="A", role=UserRole.MEMBER, token_version=2))
            db.add(User(id="u2", email="b@example.invalid", name="B", role=UserRole.MEMBER, is_active=False))
            await db.commit()
        self._p = patch("app.db.session.async_session_factory", self.Session)
        self._p.start()

    async def asyncTearDown(self):
        self._p.stop()
        await self.engine.dispose()

    async def _auth(self, token):
        return await computer_use._authenticate_ws(_Ws(), token)

    async def test_gueltiges_zugangstoken(self):
        self.assertEqual(await self._auth(create_access_token("u1", "member", token_version=2)), "u1")

    async def test_refresh_token_wird_abgelehnt(self):
        self.assertIsNone(await self._auth(create_refresh_token("u1", token_version=2)))

    async def test_widerrufene_sitzung_wird_abgelehnt(self):
        self.assertIsNone(await self._auth(create_access_token("u1", "member", token_version=1)))

    async def test_deaktiviertes_konto_wird_abgelehnt(self):
        self.assertIsNone(await self._auth(create_access_token("u2", "member")))

    async def test_unbekannter_nutzer_wird_abgelehnt(self):
        self.assertIsNone(await self._auth(create_access_token("weg", "member")))

    async def test_zwischen_token_der_zwei_faktor_anmeldung_wird_abgelehnt(self):
        from types import SimpleNamespace

        from app.core.zwei_faktor import pending_token_erstellen

        nutzer = SimpleNamespace(id="u1", token_version=2, role=UserRole.MEMBER)
        self.assertIsNone(await self._auth(pending_token_erstellen(nutzer, "verify")))


class _Antwort(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class BridgeMeldetZweiFaktor(unittest.TestCase):
    def _login(self, antwort: dict):
        with patch.object(tray_app.urllib.request, "urlopen", return_value=_Antwort(json.dumps(antwort).encode())), \
             patch.object(tray_app, "_tls_context", return_value=None):
            return tray_app.api_login("https://example.invalid", "a@example.invalid", "x")

    def test_normaler_login_liefert_token(self):
        self.assertEqual(self._login({"access_token": "t", "user": {}}), "t")

    def test_zwei_faktor_gibt_verstaendliche_meldung_statt_keyerror(self):
        with self.assertRaises(tray_app.ZweiFaktorNichtUnterstuetzt) as ctx:
            self._login({"mfa_required": True, "mfa_setup_required": False, "mfa_token": "z"})
        self.assertIn("Zwei-Faktor", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
