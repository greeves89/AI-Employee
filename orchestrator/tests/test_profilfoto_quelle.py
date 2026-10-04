"""Profilfoto ohne 404-Rauschen (#907).

Jede Seite fragte ``/auth/me/photo`` ab — auch fuer Nutzer ohne Microsoft-
Anmeldung, die nie ein Foto haben koennen. Das ergab bei jedem Laden eine 404
in der Konsole. Jetzt sagt ``/auth/me`` per ``has_photo_source``, ob es eine
Quelle gibt; der Endpunkt antwortet ohne Quelle mit 204 (kein Foto, kein Fehler).
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.auth import UserResponse, get_me_photo
from app.models.user import UserRole


def _nutzer(sso_provider=None):
    return SimpleNamespace(
        id="u1", email="u1@example.com", name="Eins", role=UserRole.MEMBER, custom_role_id=None,
        is_active=True, approved=True, last_active_at=None, monthly_cost_usd=0.0,
        tutorial_seen_at=None, sso_provider=sso_provider,
    )


class FotoQuelleImProfilTests(unittest.TestCase):
    def test_lokaler_nutzer_hat_keine_quelle(self):
        daten = UserResponse.model_validate(_nutzer()).model_dump()
        self.assertFalse(daten["has_photo_source"])

    def test_microsoft_nutzer_hat_eine_quelle(self):
        daten = UserResponse.model_validate(_nutzer("microsoft")).model_dump()
        self.assertTrue(daten["has_photo_source"])

    def test_anderer_anbieter_hat_keine_quelle(self):
        daten = UserResponse.model_validate(_nutzer("google")).model_dump()
        self.assertFalse(daten["has_photo_source"])

    def test_anbieter_selbst_wird_nicht_ausgeliefert(self):
        # Nur die Ja/Nein-Antwort geht an den Client, nicht die Anmeldeart.
        daten = UserResponse.model_validate(_nutzer("microsoft")).model_dump()
        self.assertNotIn("sso_provider", daten)


class FotoEndpunktTests(unittest.IsolatedAsyncioTestCase):
    async def test_ohne_quelle_204_statt_404(self):
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_nutzer())):
            antwort = await get_me_photo(SimpleNamespace(), SimpleNamespace())
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(antwort.body, b"")

    async def test_ohne_gueltiges_token_204(self):
        dienst = MagicMock()
        dienst.get_valid_token = AsyncMock(side_effect=RuntimeError("kein Token"))
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_nutzer("microsoft"))), \
                patch("app.services.oauth_service.OAuthService", return_value=dienst):
            antwort = await get_me_photo(SimpleNamespace(), SimpleNamespace())
        self.assertEqual(antwort.status_code, 204)


class FotoInhaltsartTests(unittest.IsolatedAsyncioTestCase):
    """Die Inhaltsart kam ungeprueft von Microsoft Graph durch — ein ``text/html``
    waere unter der Adresse der Anlage als Seite ausgeliefert worden (#907)."""

    async def _abruf(self, content_type):
        antwort_graph = SimpleNamespace(
            status_code=200, content=b"\x89PNG-Bilddaten",
            headers={"Content-Type": content_type} if content_type else {},
        )
        client = MagicMock()
        client.get = AsyncMock(return_value=antwort_graph)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        dienst = MagicMock()
        dienst.get_valid_token = AsyncMock(return_value="token")
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_nutzer("microsoft"))), \
                patch("app.services.oauth_service.OAuthService", return_value=dienst), \
                patch("httpx.AsyncClient", return_value=client):
            return await get_me_photo(SimpleNamespace(), SimpleNamespace())

    async def test_bildformate_werden_ausgeliefert(self):
        for art in ("image/jpeg", "image/png", "image/gif", "image/webp", "IMAGE/PNG; charset=binary"):
            with self.subTest(art=art):
                antwort = await self._abruf(art)
                self.assertEqual(antwort.status_code, 200)
                self.assertEqual(antwort.body, b"\x89PNG-Bilddaten")
                self.assertTrue(antwort.media_type.startswith("image/"))
                self.assertEqual(antwort.headers.get("x-content-type-options"), "nosniff")

    async def test_andere_inhaltsarten_ergeben_kein_foto(self):
        for art in ("text/html", "image/svg+xml", "application/javascript",
                    "application/octet-stream", None):
            with self.subTest(art=art):
                antwort = await self._abruf(art)
                self.assertEqual(antwort.status_code, 204)
                self.assertEqual(antwort.body, b"")


if __name__ == "__main__":
    unittest.main()
