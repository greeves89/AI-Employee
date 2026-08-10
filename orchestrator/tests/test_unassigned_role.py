"""Ohne zugewiesene Rolle bleibt die Plattform zu (Kundenmeldung 2026-08-10).

Der Befund vom Kunden: wer sich über Microsoft anmeldet, um dem M365-MCP
zuzustimmen, bekam als Nebenwirkung die **volle Oberfläche** — obwohl ihm niemand
etwas zugewiesen hatte. Zustimmen und Mitarbeiten waren dieselbe Berechtigung.

Der Kern dieser Tests ist die **Grenze**, nicht die Sperre: Oberfläche zu,
angebundene Dienste offen. Wäre nur das eine geprüft, könnte das andere still
mitkaputtgehen — und dann hätten wir statt eines zu weit offenen Zugangs einen
Kunden ohne Postfach.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.permissions import (
    DEFAULT_PERMISSIONS_BY_ROLE,
    role_for_new_user,
)
from app.dependencies import _ALLOWED_WHILE_UNASSIGNED, _is_unassigned
from app.models.user import UserRole


class RoleAssignmentTests(unittest.TestCase):
    def test_the_first_user_is_always_admin(self):
        """Sonst waere eine frische Anlage von der ersten Sekunde an ausgesperrt."""
        self.assertEqual(role_for_new_user(is_first=True), UserRole.ADMIN)

    def test_everyone_else_gets_nothing_by_default(self):
        self.assertEqual(role_for_new_user(is_first=False), UserRole.UNASSIGNED)

    def test_the_old_behaviour_is_still_reachable(self):
        """Wer es will, stellt es zurueck — aber es ist nicht mehr die Vorgabe."""
        with patch("app.config.settings.default_new_user_role", "member"):
            self.assertEqual(role_for_new_user(is_first=False), UserRole.MEMBER)

    def test_a_typo_in_the_setting_grants_nothing(self):
        """Ein Vertipper darf keine Rechte verteilen."""
        with patch("app.config.settings.default_new_user_role", "membre"):
            self.assertEqual(role_for_new_user(is_first=False), UserRole.UNASSIGNED)

    def test_admin_cannot_be_the_default(self):
        """Sonst waere die Selbstregistrierung eine Selbstbedienung."""
        with patch("app.config.settings.default_new_user_role", "admin"):
            self.assertEqual(role_for_new_user(is_first=False), UserRole.UNASSIGNED)


class PermissionTests(unittest.TestCase):
    def test_unassigned_grants_nothing_at_all(self):
        perms = DEFAULT_PERMISSIONS_BY_ROLE[UserRole.UNASSIGNED]
        self.assertEqual(perms["max_agents"], 0)
        for key, value in perms.items():
            if key == "max_agents":
                continue
            with self.subTest(key=key):
                self.assertEqual(value, [], f"{key} muss leer sein, nicht None (= alles)")

    def test_every_role_is_covered(self):
        """Eine Rolle ohne Eintrag wuerde beim Nachschlagen einen KeyError werfen."""
        for role in UserRole:
            with self.subTest(role=role):
                self.assertIn(role, DEFAULT_PERMISSIONS_BY_ROLE)


class DetectionTests(unittest.TestCase):
    def test_recognises_enum_and_plain_text(self):
        """Derselbe Nutzer kommt mal als ORM-Objekt, mal als schlichtes Abbild."""
        self.assertTrue(_is_unassigned(SimpleNamespace(role=UserRole.UNASSIGNED)))
        self.assertTrue(_is_unassigned(SimpleNamespace(role="unassigned")))

    def test_does_not_fire_for_real_roles(self):
        for role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.MEMBER, UserRole.VIEWER):
            with self.subTest(role=role):
                self.assertFalse(_is_unassigned(SimpleNamespace(role=role)))

    def test_missing_role_is_not_treated_as_unassigned(self):
        """Ein Objekt ohne Rollenfeld (Agenten-Pseudonutzer) darf hier nicht
        haengenbleiben — das waere eine Sperre am voellig falschen Ort."""
        self.assertFalse(_is_unassigned(SimpleNamespace()))
        self.assertFalse(_is_unassigned(SimpleNamespace(role=None)))


class AllowlistTests(unittest.TestCase):
    def test_only_the_bare_minimum_stays_open(self):
        for path in ("/api/v1/auth/me", "/api/v1/auth/logout",
                     "/api/v1/auth/refresh", "/api/v1/version", "/api/v1/health"):
            with self.subTest(path=path):
                self.assertTrue(_ALLOWED_WHILE_UNASSIGNED.match(path))

    def test_everything_that_matters_is_closed(self):
        for path in ("/api/v1/agents/", "/api/v1/tasks", "/api/v1/secrets",
                     "/api/v1/admin/users", "/api/v1/knowledge", "/api/v1/settings",
                     "/api/v1/ai-accounts", "/api/v1/brains"):
            with self.subTest(path=path):
                self.assertIsNone(_ALLOWED_WHILE_UNASSIGNED.match(path))

    def test_the_allowlist_cannot_be_walked_around(self):
        """Ein Praefix-Vergleich statt eines Anker-Vergleichs waere hier eine
        Einladung: /api/v1/auth/me/../../agents ist kein /auth/me."""
        for sneaky in ("/api/v1/auth/me/../../agents", "/x/api/v1/auth/me",
                       "/api/v1/authx/me", "/api/v1/agents?x=/api/v1/auth/me"):
            with self.subTest(path=sneaky):
                match = _ALLOWED_WHILE_UNASSIGNED.match(sneaky)
                self.assertTrue(
                    match is None or sneaky.startswith("/api/v1/auth/me/"),
                    f"{sneaky} darf nicht als erlaubt gelten",
                )


class GateTests(unittest.IsolatedAsyncioTestCase):
    """Der Riegel selbst — an der Stelle, durch die jede Anfrage der Oberfläche geht."""

    def _request(self, path: str):
        return SimpleNamespace(
            cookies={"access_token": "t"},
            headers={},
            url=SimpleNamespace(path=path),
        )

    async def _call(self, role, path):
        from datetime import datetime, timezone

        from app import dependencies

        # last_active_at auf JETZT: sonst schreibt get_current_user die
        # Aktivitaetsspalte und braucht dafuer eine echte Datenbank. Geprueft wird
        # hier der Riegel, nicht die Buchfuehrung.
        user = SimpleNamespace(
            id="u1", email="x@y.z", role=role, is_active=True, approved=True,
            last_active_at=datetime.now(timezone.utc),
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=user))
        with patch("app.core.auth.decode_token", return_value={"type": "access", "sub": "u1"}), \
             patch("app.db.session.set_rls_user", AsyncMock()):
            return await dependencies.get_current_user(self._request(path), db)

    async def test_unassigned_is_refused_with_a_reason(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._call(UserRole.UNASSIGNED, "/api/v1/agents/")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail["error"], "role_unassigned")
        # Die Meldung ist das, was der Nutzer liest — sie muss sagen, was zu tun ist.
        self.assertIn("Administrator", ctx.exception.detail["message"])

    async def test_unassigned_may_still_ask_who_it_is(self):
        """Sonst kann die Oberflaeche nicht einmal erklaeren, warum sie leer ist."""
        user = await self._call(UserRole.UNASSIGNED, "/api/v1/auth/me")
        self.assertEqual(user.id, "u1")

    async def test_a_real_role_passes(self):
        user = await self._call(UserRole.MEMBER, "/api/v1/agents/")
        self.assertEqual(user.id, "u1")


class McpStaysOpenTests(unittest.TestCase):
    """Die andere Haelfte der Grenze: der MCP-Weg darf NICHT mitgesperrt werden.

    Beide Wege pruefen ``is_active`` und ``approved`` — aber nicht die Rolle, und
    keiner von beiden laeuft durch ``get_current_user``. Das ist kein Zufall,
    sondern der Zweck: Postfach ja, Plattform nein. Ein Test darauf, weil eine
    spaetere „Vereinheitlichung" genau hier den Kunden ohne Postfach zuruecklassen
    wuerde.
    """

    def test_the_oauth_consent_path_does_not_use_require_auth(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/oauth_as.py").read_text()
        self.assertNotIn("Depends(require_auth)", src)
        self.assertIn("_approved_session_user_id", src)

    def test_the_session_check_looks_at_state_not_role(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/oauth_as.py").read_text()
        block = src[src.index("async def _approved_session_user_id"):]
        block = block[:block.index("\ndef ")]
        self.assertIn("is_active", block)
        self.assertIn("approved", block)
        self.assertNotIn("role", block)

    def test_the_mcp_resource_uses_a_bearer_token(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/mcp_msgraph_external.py").read_text()
        self.assertNotIn("require_auth", src)


if __name__ == "__main__":
    unittest.main()
