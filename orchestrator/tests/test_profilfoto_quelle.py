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


if __name__ == "__main__":
    unittest.main()
