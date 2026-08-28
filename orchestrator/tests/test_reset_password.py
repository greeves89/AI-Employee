"""Admin-Passwort-Reset (Teil E): Kunde hatte keinen Weg, ein vergessenes
Passwort zurueckzusetzen — kein Button, kein Endpunkt. Diese Tests decken den
neuen ``POST /auth/users/{user_id}/reset-password`` ab: admin-only, 404 fuer
unbekannte Nutzer, und dass das zurueckgegebene Klartext-Passwort tatsaechlich
gegen den neu gespeicherten Hash verifiziert (nicht nur "irgendein String").
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.api.auth import reset_user_password
from app.core.auth import verify_password
from app.models.user import UserRole


def _admin():
    return SimpleNamespace(id="admin1", email="admin@x.z", role=UserRole.ADMIN)


def _member():
    return SimpleNamespace(id="member1", email="member@x.z", role=UserRole.MEMBER)


class ResetPasswordTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_admin_is_refused(self):
        db = SimpleNamespace(get=AsyncMock(), commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_member())):
            with self.assertRaises(HTTPException) as ctx:
                await reset_user_password("target1", SimpleNamespace(), db)
        self.assertEqual(ctx.exception.status_code, 403)
        db.get.assert_not_called()

    async def test_missing_user_is_404(self):
        db = SimpleNamespace(get=AsyncMock(return_value=None), commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_admin())):
            with self.assertRaises(HTTPException) as ctx:
                await reset_user_password("ghost", SimpleNamespace(), db)
        self.assertEqual(ctx.exception.status_code, 404)
        db.commit.assert_not_called()

    async def test_success_returns_plaintext_and_replaces_the_hash(self):
        target = SimpleNamespace(id="target1", email="target@x.z", password_hash="old-hash")
        db = SimpleNamespace(get=AsyncMock(return_value=target), commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_admin())):
            result = await reset_user_password("target1", SimpleNamespace(), db)

        self.assertEqual(result["user_id"], "target1")
        self.assertEqual(result["email"], "target@x.z")
        self.assertNotEqual(target.password_hash, "old-hash")
        db.commit.assert_awaited_once()

    async def test_the_returned_password_actually_verifies(self):
        """Der Sinn der Funktion: das ausgegebene Klartext-Passwort muss das
        Nutzer-Login tatsaechlich wieder oeffnen, nicht nur irgendein String sein."""
        target = SimpleNamespace(id="target1", email="target@x.z", password_hash="old-hash")
        db = SimpleNamespace(get=AsyncMock(return_value=target), commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_admin())):
            result = await reset_user_password("target1", SimpleNamespace(), db)

        self.assertTrue(verify_password(result["temp_password"], target.password_hash))

    async def test_two_resets_produce_different_passwords(self):
        """Waere der Zufall schwach/fix, koennte ein alter Reset weiterhin gelten."""
        target = SimpleNamespace(id="target1", email="target@x.z", password_hash="old-hash")
        db = SimpleNamespace(get=AsyncMock(return_value=target), commit=AsyncMock())
        with patch("app.dependencies.get_current_user", AsyncMock(return_value=_admin())):
            first = await reset_user_password("target1", SimpleNamespace(), db)
            second = await reset_user_password("target1", SimpleNamespace(), db)

        self.assertNotEqual(first["temp_password"], second["temp_password"])


if __name__ == "__main__":
    unittest.main()
