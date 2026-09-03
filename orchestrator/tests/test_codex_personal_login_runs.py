"""Die persoenliche Codex-Anmeldung, ausgefuehrt statt gelesen.

Nutzerbericht vom 2026-08-16, mit Bildschirmfoto: Geraetecode eingegeben,
ChatGPT meldete „Seite kann geschlossen werden" — und die Oberflaeche stand
weiter auf „Warte auf die Bestaetigung…". Im Protokoll:

    GET  /api/v1/me/ai-credentials/codex/status/1441be49…  -> 404 Not Found
    Stored Codex auth.json (account: …, user: None)
    Synced Codex auth.json to shared agent volume

Ursache: ``start()`` nahm ``for_user_id`` entgegen und reichte es beim Bau der
Sitzung **nicht weiter**. Damit war ``session.for_user_id`` immer ``None``:

* die Zustandsabfrage verglich ``session.for_user_id != user.id`` -> 404,
* und der Abschluss nahm den Anlagen-Zweig — das private ChatGPT-Konto des
  Nutzers ueberschrieb per ``sync_auth_json()`` die gemeinsame Datei ALLER
  Codex-Agenten.

Die bisherigen Tests (``test_my_ai_credentials_ui``) haben den persoenlichen
Zweig im Quelltext GESUCHT und gefunden — er war nur nie erreichbar. Deshalb
laeuft hier alles wirklich: kein ``assertIn`` auf Quelltext.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.codex_device_auth_service import (
    CodexDeviceAuthService,
    CodexDeviceAuthSession,
)

#: Woertlich das, was ``codex login --device-auth`` ausgibt — der Code ist
#: der aus dem Bildschirmfoto des Nutzers.
AUSGABE = (
    "Open https://auth.openai.com/codex/device in your browser\n"
    "and enter the code: VAUF-OTVE1\n"
)


def _dienst_mit_gefaelschtem_codex():
    """``codex login --device-auth`` durch einen Prozess ersetzen, der haengt."""
    prozess = MagicMock()
    prozess.returncode = None
    prozess.wait = AsyncMock(return_value=0)
    return CodexDeviceAuthService(), prozess


class TheSessionRemembersWhoAskedTests(unittest.IsolatedAsyncioTestCase):
    """Der eigentliche Fehler: ein fallengelassenes Argument."""

    async def _start(self, for_user_id):
        dienst, prozess = _dienst_mit_gefaelschtem_codex()
        with patch("shutil.which", return_value="/usr/bin/codex"), \
             patch("asyncio.create_subprocess_exec", AsyncMock(return_value=prozess)), \
             patch.object(CodexDeviceAuthService, "_read_until_device_code",
                          AsyncMock(return_value=AUSGABE)), \
             patch.object(CodexDeviceAuthService, "_wait_and_store", AsyncMock()):
            return dienst, await dienst.start(for_user_id=for_user_id)

    async def test_a_personal_login_carries_the_user(self):
        _, sitzung = await self._start("u-42")
        self.assertEqual(sitzung.for_user_id, "u-42")

    async def test_the_platform_login_stays_ownerless(self):
        """Der Administrator-Weg darf sich durch die Korrektur nicht aendern."""
        _, sitzung = await self._start(None)
        self.assertIsNone(sitzung.for_user_id)

    async def test_the_owner_can_read_the_status_back(self):
        """Genau der 404 aus dem Bericht: die Oberflaeche fragt nach und die
        Sitzung muss demselben Nutzer gehoeren."""
        dienst, sitzung = await self._start("u-42")
        wieder = await dienst.get(sitzung.id)
        self.assertIsNotNone(wieder)
        self.assertEqual(wieder.for_user_id, "u-42")


class ThePersonalResultNeverTouchesTheSharedFileTests(unittest.IsolatedAsyncioTestCase):
    """Die gefaehrliche Nebenwirkung.

    Alle Codex-Agenten teilen sich EINE ``auth.json`` mit einem rotierenden
    Aktualisierungs-Token. Wer dort das private Konto eines Nutzers
    hineinschreibt, stellt die ganze Flotte um.
    """

    def _sitzung(self, for_user_id):
        heim = tempfile.mkdtemp(prefix="codex-test-")
        with open(os.path.join(heim, "auth.json"), "w") as f:
            json.dump({"tokens": {"refresh_token": "geheim"}}, f)
        prozess = MagicMock()
        prozess.returncode = 0
        prozess.wait = AsyncMock(return_value=0)
        from datetime import datetime, timedelta, timezone
        return CodexDeviceAuthSession(
            id="s1", code="VAUF-OTVE1",
            verification_uri="https://auth.openai.com/codex/device",
            codex_home=heim,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            for_user_id=for_user_id, process=prozess,
        )

    async def _abschluss(self, for_user_id):
        dienst = CodexDeviceAuthService()
        sitzung = self._sitzung(for_user_id)
        sync = AsyncMock()
        db = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)))
        db.__aenter__ = AsyncMock(return_value=db)
        db.__aexit__ = AsyncMock(return_value=False)
        speichern = AsyncMock(return_value=MagicMock(account_label="anlage@example.com"))
        with patch("app.services.codex_device_auth_service.async_session_factory",
                   MagicMock(return_value=db)), \
             patch("app.services.codex_auth_service.CodexAuthService.sync_auth_json", sync), \
             patch("app.services.oauth_service.OAuthService.store_auth_json", speichern), \
             patch("app.core.encryption.encrypt_token", lambda s: "verschluesselt"):
            await dienst._wait_and_store(sitzung)
        return sitzung, sync, speichern, db

    async def test_a_personal_login_does_not_sync_the_fleet(self):
        sitzung, sync, speichern, _ = await self._abschluss("u-42")
        self.assertEqual(sitzung.status, "connected", sitzung.error)
        sync.assert_not_awaited()
        speichern.assert_not_awaited()

    async def test_a_personal_login_is_written_to_the_users_own_row(self):
        _, _, _, db = await self._abschluss("u-42")
        db.add.assert_called_once()
        zeile = db.add.call_args[0][0]
        self.assertEqual(zeile.user_id, "u-42")
        self.assertEqual(zeile.harness, "codex")

    async def test_the_platform_login_still_syncs(self):
        """Der Anlagen-Weg braucht die gemeinsame Datei — sonst haetten die
        Agenten keinen Zugang mehr."""
        sitzung, sync, speichern, _ = await self._abschluss(None)
        self.assertEqual(sitzung.status, "connected", sitzung.error)
        sync.assert_awaited_once()
        speichern.assert_awaited_once()

    async def test_the_temporary_home_is_removed_either_way(self):
        """Dort liegt ein gueltiger Aktualisierungs-Token im Klartext."""
        sitzung, _, _, _ = await self._abschluss("u-42")
        self.assertFalse(os.path.exists(sitzung.codex_home))


if __name__ == "__main__":
    unittest.main()
