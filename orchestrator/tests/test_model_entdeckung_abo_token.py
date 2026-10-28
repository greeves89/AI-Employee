"""Modell-Erkennung mit Abo-Anmeldung: welches Kopffeld bekommt welcher Schlüssel.

Ein Abo-Token aus ``claude setup-token`` beginnt mit ``sk-ant-oat…`` — also
AUCH mit ``sk-ant-``. Die Erkennung hielt deshalb jedes Abo-Token für einen
API-Schlüssel, schickte es als ``x-api-key`` und bekam 401 zurück: „Jetzt neu
erkennen“ fand auf einer Anlage mit Abo-Anmeldung nie ein neues Modell
(Sonnet 5.5, Haiku 5.5, Fable 5.1 fehlten). Nur ``sk-ant-api…`` ist ein
API-Schlüssel; dieselbe Unterscheidung trifft ``agent_credentials.py``.
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models.platform_settings import PlatformSettings
from app.services import model_registry_service as mrs
from app.services.model_registry_service import CACHE_KEY, Abruf, discover
from app.services.settings_service import SettingsService

ABO_TOKEN = "sk-ant-oat01-" + "x" * 90
API_SCHLUESSEL = "sk-ant-api03-" + "y" * 90


class KopffeldTests(unittest.IsolatedAsyncioTestCase):
    async def _kopf(self, db_token):
        with patch.object(settings, "anthropic_api_key", ""), \
             patch.object(settings, "claude_code_oauth_token", ""), \
             patch("app.services.claude_token_service.ClaudeTokenService._get_db_token",
                   AsyncMock(return_value=db_token)):
            return await mrs._anthropic_auth_headers()

    async def test_abo_token_geht_als_bearer_mit_oauth_kennung(self):
        kopf = await self._kopf(ABO_TOKEN)
        self.assertEqual(kopf["authorization"], f"Bearer {ABO_TOKEN}")
        self.assertEqual(kopf["anthropic-beta"], "oauth-2025-04-20")
        self.assertNotIn("x-api-key", kopf)

    async def test_api_schluessel_geht_als_x_api_key(self):
        kopf = await self._kopf(API_SCHLUESSEL)
        self.assertEqual(kopf["x-api-key"], API_SCHLUESSEL)
        self.assertNotIn("authorization", kopf)

    async def test_ohne_jeden_zugang_kein_kopf(self):
        self.assertIsNone(await self._kopf(None))


def _treffer(mid):
    return {"mode": "claude_code", "provider": "anthropic", "value": mid,
            "label": mid, "tier": "Discovered", "source": "discovered"}


class DatumsfassungTests(unittest.IsolatedAsyncioTestCase):
    """Anthropic meldet aeltere Modelle zusaetzlich mit Datum im Namen. Steht das
    Modell ohne Datum schon in der Basisliste, darf es nicht ein zweites Mal als
    „Erkannt“ erscheinen — neue Modelle aber sehr wohl."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                PlatformSettings.metadata.create_all, tables=[PlatformSettings.__table__]
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_datumsfassung_eines_basismodells_faellt_weg(self):
        gefunden = [_treffer(m) for m in (
            "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1",
            "claude-haiku-4-5-20251001", "claude-opus-5-5",
        )]
        async with self.Session() as db:
            with patch.object(mrs, "_discover_anthropic",
                              AsyncMock(return_value=Abruf(gefunden, gescheitert=False))), \
                 patch.object(mrs, "_anthropic_auth_headers",
                              AsyncMock(return_value={"x-api-key": "k"})), \
                 patch.object(settings, "foundry_api_key", ""):
                await discover(db)
            stand = json.loads(await SettingsService(db).get(CACHE_KEY))
        werte = sorted(m["value"] for m in stand["models"])
        # opus-5-5 steht schon in der Basis, haiku-4-5 ebenso (ohne Datum).
        self.assertEqual(werte, ["claude-fable-5-1", "claude-haiku-5-5", "claude-sonnet-5-5"])


if __name__ == "__main__":
    unittest.main()
