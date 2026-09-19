"""Admin-Websuche-Provider (Admin -> Websuche, Vorbild OpenWebUI).

Vorher gab es ZWEI unabhaengige, sich widersprechende DuckDuckGo-Kopien
(Sprachfront POST, Agent-Container GET) und keine echte Brave/SerpApi-
Anbindung ueberhaupt (der "brave-search"-AI-Account-Typ war ein reiner Stub).
Diese Tests decken den neuen, gemeinsamen Provider-Dispatch in
``app.core.web_search`` sowie die Einstellungs-Rundreise in ``app.api.settings``.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.settings import get_settings, update_settings
from app.core.web_search import web_search, web_search_with_settings
from app.models.oauth_integration import OAuthIntegration
from app.models.platform_settings import PlatformSettings
from app.schemas.settings import SettingsUpdate
from app.services.settings_service import SettingsService


def _client_returning(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _admin():
    return SimpleNamespace(id="admin-1", role="admin", email="admin@example.test")


class ProviderDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_provider_uses_duckduckgo(self):
        ctx, client = _client_returning({}, status=200)
        with patch("httpx.AsyncClient", return_value=ctx):
            client.post.return_value.text = ""
            await web_search("pokemon karten", 5)
        client.post.assert_called_once()
        self.assertIn("html.duckduckgo.com", client.post.call_args.args[0])

    async def test_brave_provider_calls_brave_with_the_subscription_header(self):
        payload = {"web": {"results": [
            {"title": "Pokemon Karten kaufen", "url": "https://example.test/p", "description": "Sammelkarten"},
        ]}}
        ctx, client = _client_returning(payload)
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("pokemon karten", 5, provider="brave", api_key="bk")
        self.assertEqual(client.get.call_args.args[0], "https://api.search.brave.com/res/v1/web/search")
        self.assertEqual(client.get.call_args.kwargs["headers"]["X-Subscription-Token"], "bk")
        # ``age`` kommt seit der Brave-News-Erweiterung mit: die Websuche liefert
        # ``page_age``, das vorher stillschweigend verworfen wurde. Hier ist es
        # leer, weil die Testantwort kein Datum enthaelt.
        self.assertEqual(out, [{
            "title": "Pokemon Karten kaufen",
            "url": "https://example.test/p",
            "snippet": "Sammelkarten",
            "age": "",
        }])

    async def test_serp_provider_calls_serpapi_with_the_key_param(self):
        payload = {"organic_results": [
            {"title": "Pokemon News", "link": "https://example.test/n", "snippet": "Neuigkeiten"},
        ]}
        ctx, client = _client_returning(payload)
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("pokemon karten", 5, provider="serp", api_key="sk")
        self.assertEqual(client.get.call_args.args[0], "https://serpapi.com/search")
        self.assertEqual(client.get.call_args.kwargs["params"]["api_key"], "sk")
        self.assertEqual(out, [{"title": "Pokemon News", "url": "https://example.test/n", "snippet": "Neuigkeiten"}])

    async def test_brave_without_a_key_falls_back_to_duckduckgo(self):
        """Ein Admin, der den Provider waehlt aber den Key vergisst, soll nicht
        stumm leer bleiben — DuckDuckGo bleibt der ehrliche Rueckfallweg."""
        ctx, client = _client_returning({}, status=200)
        client.post.return_value.text = ""
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("pokemon karten", 5, provider="brave", api_key="")
        client.post.assert_called_once()
        client.get.assert_not_called()

    async def test_empty_query_returns_nothing_without_any_http_call(self):
        with patch("httpx.AsyncClient") as mocked:
            out = await web_search("   ", 5)
        self.assertEqual(out, [])
        mocked.assert_not_called()

    async def test_a_broken_brave_response_yields_an_empty_list_not_a_crash(self):
        ctx, client = _client_returning({})
        client.get = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("pokemon karten", 5, provider="brave", api_key="bk")
        self.assertEqual(out, [])


class WebSearchWithSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(PlatformSettings.metadata.create_all, tables=[PlatformSettings.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_reads_the_persisted_provider_and_key(self):
        async with self.Session() as db:
            svc = SettingsService(db)
            await svc.set("web_search_provider", "brave")
            await svc.set("web_search_api_key", "bk")
            await db.commit()

        payload = {"web": {"results": []}}
        ctx, client = _client_returning(payload)
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await web_search_with_settings("pokemon karten", 5, db)
        self.assertEqual(client.get.call_args.kwargs["headers"]["X-Subscription-Token"], "bk")

    async def test_no_persisted_provider_defaults_to_duckduckgo(self):
        ctx, client = _client_returning({}, status=200)
        client.post.return_value.text = ""
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await web_search_with_settings("pokemon karten", 5, db)
        client.post.assert_called_once()


class SettingsRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (PlatformSettings, OAuthIntegration):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_default_is_duckduckgo_with_no_key_configured(self):
        async with self.Session() as db:
            resp = await get_settings(user=_admin(), db=db)
        self.assertEqual(resp.web_search_provider, "duckduckgo")
        self.assertFalse(resp.has_web_search_api_key)

    async def test_switching_to_brave_with_a_key_persists_and_is_read_back(self):
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(web_search_provider="brave", web_search_api_key="bk"),
                user=_admin(), db=db,
            )
        async with self.Session() as db:
            resp = await get_settings(user=_admin(), db=db)
        self.assertEqual(resp.web_search_provider, "brave")
        self.assertTrue(resp.has_web_search_api_key)

    async def test_the_key_itself_is_never_returned_in_the_response(self):
        """SECRET_KEYS: der Klartext-Key darf niemals aus GET /settings/ zurueckkommen."""
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(web_search_provider="brave", web_search_api_key="super-secret"),
                user=_admin(), db=db,
            )
            resp = await get_settings(user=_admin(), db=db)
        self.assertNotIn("super-secret", str(resp.model_dump()))

    async def test_an_unknown_provider_is_rejected(self):
        from fastapi import HTTPException
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as cm:
                await update_settings(
                    SettingsUpdate(web_search_provider="bing"), user=_admin(), db=db,
                )
        self.assertEqual(cm.exception.status_code, 422)


class VoiceWebSearchUsesTheConfiguredProviderTests(unittest.IsolatedAsyncioTestCase):
    """Die Sprachfront hatte DuckDuckGo fest verdrahtet — ``_web_search`` liest
    jetzt denselben admin-konfigurierten Provider wie Agent-Container und MCP."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(PlatformSettings.metadata.create_all, tables=[PlatformSettings.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_voice_search_honours_the_admin_configured_brave_provider(self):
        from app.services.realtime_voice_session import RealtimeVoiceSession

        async with self.Session() as db:
            svc = SettingsService(db)
            await svc.set("web_search_provider", "brave")
            await svc.set("web_search_api_key", "bk")
            await db.commit()

        v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        v._emit = AsyncMock()

        payload = {"web": {"results": [
            {"title": "Pokemon Karten", "url": "https://example.test/p", "description": "Sammelkarten"},
        ]}}
        ctx, client = _client_returning(payload)
        with patch("app.db.session.async_session_factory", self.Session), \
             patch("httpx.AsyncClient", return_value=ctx):
            out = await v._web_search("pokemon karten", 5)

        self.assertEqual(client.get.call_args.args[0], "https://api.search.brave.com/res/v1/web/search")
        self.assertIn("Pokemon Karten", out)
        v._emit.assert_awaited_once()


class BraveNewsProviderTests(unittest.IsolatedAsyncioTestCase):
    """Brave News — eigener Index, liefert Datum und Herausgeber mit.

    Ohne Datum kann ein Agent, der ueber aktuelle Ereignisse schreibt, einen
    zwei Jahre alten Artikel nicht von der Meldung von heute unterscheiden.
    Genau deshalb gibt es diesen Provider zusaetzlich zur Websuche.
    """

    async def test_hits_the_news_endpoint_and_keeps_age_and_publisher(self):
        payload = {
            "results": [
                {
                    "title": "Gold hits record",
                    "url": "https://example.test/gold",
                    "description": "Spot gold rose.",
                    "age": "3 hours ago",
                    "meta_url": {"hostname": "reuters.com"},
                }
            ]
        }
        ctx, client = _client_returning(payload)
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("gold", 5, provider="brave_news", api_key="bk")

        self.assertIn("news/search", client.get.call_args.args[0])
        self.assertEqual(client.get.call_args.kwargs["headers"]["X-Subscription-Token"], "bk")
        self.assertEqual(out[0]["age"], "3 hours ago")
        self.assertEqual(out[0]["publisher"], "reuters.com")
        self.assertEqual(out[0]["url"], "https://example.test/gold")

    async def test_valid_freshness_is_passed_through(self):
        ctx, client = _client_returning({"results": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key="bk", freshness="pw")
        self.assertEqual(client.get.call_args.kwargs["params"]["freshness"], "pw")

    async def test_invalid_freshness_is_dropped_not_forwarded(self):
        """Ein Tippfehler darf nicht die ganze Anfrage mit 422 scheitern lassen."""
        ctx, client = _client_returning({"results": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key="bk", freshness="letzte-woche")
        self.assertNotIn("freshness", client.get.call_args.kwargs["params"])

    async def test_date_range_freshness_is_accepted(self):
        ctx, client = _client_returning({"results": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search(
                "gold", 5, provider="brave_news", api_key="bk",
                freshness="2026-01-01to2026-02-01",
            )
        self.assertEqual(
            client.get.call_args.kwargs["params"]["freshness"], "2026-01-01to2026-02-01",
        )

    async def test_without_key_it_falls_back_instead_of_returning_nothing(self):
        ctx, client = _client_returning({}, status=200)
        client.post.return_value.text = ""
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key=None)
        self.assertTrue(client.post.called, "ohne Key wird auf DuckDuckGo zurueckgefallen")


class BraveWebSearchAgeTests(unittest.IsolatedAsyncioTestCase):
    """Die Websuche lieferte ``page_age`` schon immer mit — es wurde nur verworfen."""

    async def test_page_age_is_no_longer_dropped(self):
        payload = {
            "web": {
                "results": [
                    {
                        "title": "Gold",
                        "url": "https://example.test/g",
                        "description": "d",
                        "page_age": "2026-09-18T10:00:00",
                    }
                ]
            }
        }
        ctx, _ = _client_returning(payload)
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("gold", 5, provider="brave", api_key="bk")
        self.assertEqual(out[0]["age"], "2026-09-18T10:00:00")


if __name__ == "__main__":
    unittest.main()
