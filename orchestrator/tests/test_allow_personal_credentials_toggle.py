"""'Eigene KI-Zugaenge erlauben' liess sich nicht deaktivieren — Befund beim Kunden.

`allow_personal_credentials` fehlte in `_FIELD_MAP` (app/api/settings.py):
die PATCH-Schleife ueberspringt jedes Feld, das dort nicht steht, lautlos —
kein Fehler, einfach nichts passiert. `GET /settings/` setzte das Feld nie im
`SettingsResponse(...)`-Konstruktor, also blieb immer der Pydantic-Schema-
Default (`True`) stehen, unabhaengig vom echten Wert. Der eigentliche
Freigabe-Check (`agent_credentials.py::personal_credentials_allowed`) liest
`settings.allow_personal_credentials` direkt vom Singleton — ohne den
`_FIELD_MAP`-Eintrag kam ein Admin-Klick dort NIE an.
"""

import unittest
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.settings import _FIELD_MAP, get_settings, update_settings
from app.config import settings as _config_settings
from app.models.oauth_integration import OAuthIntegration
from app.models.platform_settings import PlatformSettings
from app.schemas.settings import SettingsUpdate
from app.services.settings_service import SettingsService


def _admin():
    return SimpleNamespace(id="admin-1", role="admin", email="admin@example.test")


class AllowPersonalCredentialsToggleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (PlatformSettings, OAuthIntegration):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        # Singleton nicht ueber Tests hinweg verschleppen.
        self._original = _config_settings.allow_personal_credentials

    async def asyncTearDown(self):
        _config_settings.allow_personal_credentials = self._original
        await self.engine.dispose()

    def test_the_field_is_registered_so_patch_does_not_silently_drop_it(self):
        """Die eigentliche Regression: ohne diesen Eintrag tut PATCH nichts."""
        self.assertIn("allow_personal_credentials", _FIELD_MAP)

    async def test_turning_it_off_actually_persists_and_is_read_back(self):
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(allow_personal_credentials=False), user=_admin(), db=db,
            )
        async with self.Session() as db:
            resp = await get_settings(user=_admin(), db=db)
        self.assertFalse(resp.allow_personal_credentials)
        self.assertFalse(_config_settings.allow_personal_credentials)

    async def test_turning_it_on_again_also_persists(self):
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(allow_personal_credentials=False), user=_admin(), db=db,
            )
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(allow_personal_credentials=True), user=_admin(), db=db,
            )
        async with self.Session() as db:
            resp = await get_settings(user=_admin(), db=db)
        self.assertTrue(resp.allow_personal_credentials)

    async def test_survives_a_process_restart_via_the_generic_db_reload(self):
        """load_into_config() ist der generische Start-Hydrations-Weg — sobald
        einmal ueber PATCH gespeichert wurde, muss ein frischer Prozess (hier
        simuliert: Singleton manuell zurueckgesetzt) den Wert wiederfinden."""
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(allow_personal_credentials=False), user=_admin(), db=db,
            )
        _config_settings.allow_personal_credentials = True  # Neustart simulieren
        async with self.Session() as db:
            await SettingsService(db).load_into_config()
        self.assertFalse(_config_settings.allow_personal_credentials)

    async def test_omitting_the_field_leaves_the_stored_value_untouched(self):
        """PATCH mit anderen Feldern darf diesen Schalter nicht versehentlich
        zuruecksetzen."""
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(allow_personal_credentials=False), user=_admin(), db=db,
            )
        async with self.Session() as db:
            await update_settings(SettingsUpdate(max_turns=50), user=_admin(), db=db)
        async with self.Session() as db:
            resp = await get_settings(user=_admin(), db=db)
        self.assertFalse(resp.allow_personal_credentials)


if __name__ == "__main__":
    unittest.main()
