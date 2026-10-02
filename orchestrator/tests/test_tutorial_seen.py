"""Willkommensfenster mit den Klick-Tutorials: ``POST /auth/me/tutorial-seen``.

Das Fenster oeffnet sich, solange ``tutorial_seen_at`` leer ist. Der Endpunkt
setzt den Zeitpunkt fuer den ANGEMELDETEN Nutzer — ohne Anmeldung kommt die 401
von ``get_current_user`` durch, ein zweiter Aufruf aendert den ersten Zeitpunkt
nicht, und ``GET /auth/me`` liefert das Feld aus.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.auth import UserResponse, mark_tutorial_seen
from app.models.user import UserRole


def _nutzer(gesehen=None):
    return SimpleNamespace(
        id="u1", email="u1@example.com", name="Eins", role=UserRole.MEMBER, custom_role_id=None,
        is_active=True, approved=True, last_active_at=None, monthly_cost_usd=0.0, tutorial_seen_at=gesehen,
    )


class TutorialGesehenTests(unittest.IsolatedAsyncioTestCase):
    async def test_ohne_anmeldung_401(self):
        db = SimpleNamespace(commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(side_effect=HTTPException(status_code=401))):
            with self.assertRaises(HTTPException) as ctx:
                await mark_tutorial_seen(SimpleNamespace(), db)
        self.assertEqual(ctx.exception.status_code, 401)
        db.commit.assert_not_awaited()

    async def test_setzt_den_zeitpunkt_beim_eigenen_nutzer(self):
        nutzer, db = _nutzer(), SimpleNamespace(commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=nutzer)):
            antwort = await mark_tutorial_seen(SimpleNamespace(), db)
        self.assertIsNotNone(nutzer.tutorial_seen_at)
        self.assertEqual(antwort["tutorial_seen_at"], nutzer.tutorial_seen_at.isoformat())
        db.commit.assert_awaited_once()

    async def test_zweiter_aufruf_aendert_nichts(self):
        frueher = datetime(2026, 9, 1, tzinfo=timezone.utc)
        nutzer, db = _nutzer(frueher), SimpleNamespace(commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=nutzer)):
            antwort = await mark_tutorial_seen(SimpleNamespace(), db)
        self.assertEqual(nutzer.tutorial_seen_at, frueher)
        self.assertEqual(antwort["tutorial_seen_at"], frueher.isoformat())
        db.commit.assert_not_awaited()

    def test_me_liefert_das_feld(self):
        self.assertIsNone(UserResponse.model_validate(_nutzer()).model_dump()["tutorial_seen_at"])
        gesehen = datetime(2026, 10, 2, tzinfo=timezone.utc)
        self.assertEqual(UserResponse.model_validate(_nutzer(gesehen)).model_dump()["tutorial_seen_at"], gesehen)


if __name__ == "__main__":
    unittest.main()
