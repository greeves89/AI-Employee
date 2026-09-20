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
from app.core.permissions import can_use_search_index
from app.core.web_search import (
    news_search_with_settings,
    web_search,
    web_search_with_settings,
)
from fastapi import HTTPException

from app.models.oauth_integration import OAuthIntegration
from app.models.agent import Agent
from app.models.user import User, UserRole
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

    async def test_persisted_freshness_reaches_the_brave_news_provider(self):
        """Die eigentliche Luecke aus dem Review: zwei stille Mutationen (hier
        entfernt: das Weiterreichen von freshness in web_search_with_settings,
        und in SettingsRoundtripTests: das Speichern von web_search_freshness)
        blieben ohne diesen End-zu-End-Test unbemerkt gruen."""
        async with self.Session() as db:
            svc = SettingsService(db)
            await svc.set("web_search_provider", "brave_news")
            await svc.set("web_search_api_key", "bk")
            await svc.set("web_search_freshness", "pw")
            await db.commit()

        ctx, client = _client_returning({"results": []})
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await web_search_with_settings("pokemon karten", 5, db)
        self.assertEqual(client.get.call_args.kwargs["params"]["freshness"], "pw")


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

    async def test_freshness_is_persisted_and_read_back(self):
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(web_search_provider="brave_news", web_search_freshness="pm"),
                user=_admin(), db=db,
            )
        async with self.Session() as db:
            resp = await get_settings(user=_admin(), db=db)
        self.assertEqual(resp.web_search_freshness, "pm")

    async def test_an_invalid_freshness_is_rejected_at_write_time_not_silently_stored(self):
        """Vorher wurde ein ungueltiger Wert klaglos gespeichert und erst beim
        naechsten Suchaufruf verworfen — sichtbar nur im Server-Log, nicht fuer
        den Admin, der ihn eingetragen hat."""
        from fastapi import HTTPException
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as cm:
                await update_settings(
                    SettingsUpdate(web_search_freshness="letzte-woche"), user=_admin(), db=db,
                )
        self.assertEqual(cm.exception.status_code, 422)

    async def test_clearing_freshness_with_an_empty_string_is_allowed(self):
        async with self.Session() as db:
            await update_settings(
                SettingsUpdate(web_search_freshness=""), user=_admin(), db=db,
            )


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

    async def test_nonexistent_calendar_date_is_rejected(self):
        """Der Regex allein akzeptierte 2026-02-30 (den 30. Februar) — das
        Format stimmt, das Datum existiert nicht."""
        ctx, client = _client_returning({"results": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key="bk", freshness="2026-02-30to2026-03-01")
        self.assertNotIn("freshness", client.get.call_args.kwargs["params"])

    async def test_a_range_ending_before_it_starts_is_rejected(self):
        ctx, client = _client_returning({"results": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key="bk", freshness="2026-12-31to2026-01-01")
        self.assertNotIn("freshness", client.get.call_args.kwargs["params"])

    async def test_a_range_of_equal_start_and_end_is_accepted(self):
        """Start == Ende ist ein gueltiger Ein-Tages-Bereich, keine Grenzfall-Ablehnung."""
        ctx, client = _client_returning({"results": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key="bk", freshness="2026-01-01to2026-01-01")
        self.assertEqual(client.get.call_args.kwargs["params"]["freshness"], "2026-01-01to2026-01-01")

    async def test_unicode_digits_are_rejected_not_silently_normalised(self):
        """``re.fullmatch(r"\\d")`` matcht ohne re.ASCII auch Unicode-Ziffern
        (z.B. Devanagari) — date.fromisoformat wuerde daran ohnehin scheitern,
        aber das Format soll erst gar nicht als "syntaktisch gueltig" gelten."""
        ctx, client = _client_returning({"results": []})
        unicode_range = "२०२६-01-01to2026-02-01"  # führende Ziffern in Devanagari
        with patch("httpx.AsyncClient", return_value=ctx):
            await web_search("gold", 5, provider="brave_news", api_key="bk", freshness=unicode_range)
        self.assertNotIn("freshness", client.get.call_args.kwargs["params"])

    async def test_malformed_json_shape_yields_empty_list_not_a_crash(self):
        """{"results": [null]} und eine Antwort ohne meta_url duerfen keinen
        AttributeError werfen — "Never raises" gilt auch fuer kaputte/fremde
        API-Antworten, nicht nur fuer Netzwerkfehler."""
        ctx, client = _client_returning({"results": [None, {"title": "x", "url": "u"}]})
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("gold", 5, provider="brave_news", api_key="bk")
        self.assertEqual(out, [{"title": "x", "url": "u", "snippet": "", "age": "", "publisher": ""}])

    async def test_response_that_is_not_an_object_yields_empty_list(self):
        ctx, client = _client_returning(["not", "an", "object"])
        with patch("httpx.AsyncClient", return_value=ctx):
            out = await web_search("gold", 5, provider="brave_news", api_key="bk")
        self.assertEqual(out, [])


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


class RolePermissionShapeTests(unittest.TestCase):
    """Jede Rolle muss JEDES Recht nennen — auch mit leerer Liste.

    Anlass: ``search_indexes`` fehlte zunaechst bei VIEWER, weil die Zeile
    darueber einen nachgestellten Kommentar trug. Ein fehlender Schluessel
    gilt als "None = alles erlaubt" — ein Nur-Lese-Nutzer haette den
    Nachrichtenindex also nutzen duerfen. Der Fehler ist lautlos: Es gibt
    keine Fehlermeldung, nur mehr Rechte als gedacht. Dieser Test macht ihn
    laut, fuer jedes kuenftige Recht mit.
    """

    def test_every_role_declares_every_permission(self):
        from app.core.permissions import DEFAULT_PERMISSIONS_BY_ROLE

        rollen = DEFAULT_PERMISSIONS_BY_ROLE
        alle_rechte = set().union(*(set(p) for p in rollen.values()))
        for rolle, rechte in rollen.items():
            with self.subTest(rolle=rolle):
                self.assertEqual(
                    alle_rechte - set(rechte), set(),
                    f"{rolle} nennt nicht alle Rechte — fehlende gelten als unbeschraenkt",
                )

    def test_restricted_roles_do_not_get_the_news_index(self):
        from app.core.permissions import DEFAULT_PERMISSIONS_BY_ROLE, UserRole

        for rolle in (UserRole.VIEWER, UserRole.UNASSIGNED):
            with self.subTest(rolle=rolle):
                self.assertFalse(
                    can_use_search_index(DEFAULT_PERMISSIONS_BY_ROLE[rolle], "news"),
                )


class SearchIndexPermissionTests(unittest.TestCase):
    """Der Nachrichtenindex ist etwas, das der Admin freigibt — nicht etwas,
    das ein Nutzer sich einstellt. Gleiche Logik wie bei allen anderen
    Erlaubnislisten: None = alles erlaubt, eine Liste schraenkt ein."""

    def test_none_allows_everything(self):
        self.assertTrue(can_use_search_index({"search_indexes": None}, "news"))
        self.assertTrue(can_use_search_index({"search_indexes": None}, "web"))

    def test_a_list_restricts(self):
        perms = {"search_indexes": ["web"]}
        self.assertTrue(can_use_search_index(perms, "web"))
        self.assertFalse(can_use_search_index(perms, "news"))

    def test_an_empty_list_denies_everything(self):
        self.assertFalse(can_use_search_index({"search_indexes": []}, "news"))

    def test_a_missing_key_behaves_like_none(self):
        """Bestehende Installationen haben den Schluessel nicht — sie duerfen alles."""
        self.assertTrue(can_use_search_index({}, "news"))

    def test_no_index_asked_for_is_always_fine(self):
        self.assertTrue(can_use_search_index({"search_indexes": []}, None))


class NewsSearchWithSettingsTests(unittest.IsolatedAsyncioTestCase):
    """Die Nachrichtensuche ist ein eigener Weg, keine Variante der Websuche."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(PlatformSettings.metadata.create_all, tables=[PlatformSettings.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _mit_einstellungen(self, **werte):
        async with self.Session() as db:
            svc = SettingsService(db)
            for k, v in werte.items():
                await svc.set(k, v)
            await db.commit()

    async def test_it_hits_the_news_index_even_when_the_web_provider_is_duckduckgo(self):
        """Der eingestellte Web-Provider darf die Nachrichtensuche nicht umlenken."""
        await self._mit_einstellungen(web_search_provider="duckduckgo", web_search_api_key="bk")
        ctx, client = _client_returning({"results": []})
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await news_search_with_settings("ezb zinsen", 5, db)
        self.assertIn("news/search", client.get.call_args.args[0])

    async def test_the_platform_freshness_is_applied(self):
        await self._mit_einstellungen(web_search_api_key="bk", web_search_freshness="pw")
        ctx, client = _client_returning({"results": []})
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await news_search_with_settings("ezb zinsen", 5, db)
        self.assertEqual(client.get.call_args.kwargs["params"]["freshness"], "pw")

    async def test_without_a_key_it_returns_nothing_instead_of_web_results(self):
        """Stillschweigend Web-Treffer zu liefern waere schlimmer als nichts:
        der Aufrufer haelt sie sonst fuer datierte Meldungen."""
        await self._mit_einstellungen(web_search_provider="duckduckgo")
        with patch("httpx.AsyncClient") as mocked:
            async with self.Session() as db:
                out = await news_search_with_settings("ezb zinsen", 5, db)
        self.assertEqual(out, [])
        mocked.assert_not_called()

    async def test_an_empty_query_makes_no_request(self):
        await self._mit_einstellungen(web_search_api_key="bk")
        with patch("httpx.AsyncClient") as mocked:
            async with self.Session() as db:
                out = await news_search_with_settings("   ", 5, db)
        self.assertEqual(out, [])
        mocked.assert_not_called()


def _abhaengigkeiten(router, pfad: str, methode: str) -> list[str]:
    """Namen der Depends(...)-Aufrufe einer Route — wie in test_security_hardening."""
    namen: list[str] = []
    for route in router.routes:
        if getattr(route, "path", "").endswith(pfad) and methode in (getattr(route, "methods", set()) or set()):
            for dep in route.dependant.dependencies:
                call = getattr(dep, "call", None)
                if call is not None:
                    namen.append(call.__name__)
    return namen


class NewsRouteAuthTests(unittest.TestCase):
    """Beide neuen Routen haengen am Agenten-Token — unangemeldet geht nichts."""

    def test_the_news_route_requires_an_agent_token(self):
        from app.api import agent_search
        self.assertIn("verify_agent_token", _abhaengigkeiten(agent_search.router, "/news", "POST"))

    def test_the_capabilities_route_requires_an_agent_token(self):
        from app.api import agent_search
        self.assertIn("verify_agent_token", _abhaengigkeiten(agent_search.router, "/capabilities", "GET"))


class NewsRouteEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """Die Durchsetzung sitzt in der Route, nicht nur im Werkzeugkatalog.

    Dass der MCP-Server das Werkzeug ausblendet, ist Hoeflichkeit. Verlassen
    darf man sich nur darauf, dass der Endpunkt selbst ablehnt — ein Agent
    kann HTTP sprechen, auch ohne dass ihm jemand ein Werkzeug anbietet.
    """

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for modell in (PlatformSettings, Agent, User):
                await conn.run_sync(modell.metadata.create_all, tables=[modell.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _aufbauen(self, rolle, *, mit_schluessel=True, benutzer_anlegen=True):
        async with self.Session() as db:
            if mit_schluessel:
                await SettingsService(db).set("web_search_api_key", "bk")
            if benutzer_anlegen:
                db.add(User(id="u1", email="u@example.test", name="U", role=rolle))
            db.add(Agent(id="a1", name="Test", user_id="u1", config={}))
            await db.commit()

    async def test_news_is_refused_with_403_when_the_index_is_not_granted(self):
        from app.api.agent_search import AgentWebSearchRequest, agent_news_search
        await self._aufbauen(UserRole.VIEWER)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await agent_news_search(
                    AgentWebSearchRequest(query="gold"), {"agent_id": "a1"}, db,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_news_is_served_when_granted(self):
        from app.api.agent_search import AgentWebSearchRequest, agent_news_search
        await self._aufbauen(UserRole.MEMBER)
        ctx_http, client = _client_returning({"results": []})
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx_http):
                out = await agent_news_search(
                    AgentWebSearchRequest(query="gold"), {"agent_id": "a1"}, db,
                )
        self.assertEqual(out, {"results": []})
        self.assertIn("news/search", client.get.call_args.args[0])

    async def test_a_dangling_user_reference_is_refused(self):
        """user_id gesetzt, Nutzer geloescht: kein Weg zu mehr Rechten als der
        lebende Nutzer hatte."""
        from app.api.agent_search import AgentWebSearchRequest, agent_news_search
        await self._aufbauen(UserRole.MEMBER, benutzer_anlegen=False)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await agent_news_search(
                    AgentWebSearchRequest(query="gold"), {"agent_id": "a1"}, db,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_an_unknown_agent_is_refused(self):
        from app.api.agent_search import AgentWebSearchRequest, agent_news_search
        await self._aufbauen(UserRole.MEMBER)
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await agent_news_search(
                    AgentWebSearchRequest(query="gold"), {"agent_id": "gibt-es-nicht"}, db,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_capabilities_reports_news_false_when_not_granted(self):
        from app.api.agent_search import agent_search_capabilities
        await self._aufbauen(UserRole.VIEWER)
        async with self.Session() as db:
            out = await agent_search_capabilities({"agent_id": "a1"}, db)
        self.assertEqual(out, {"web": True, "news": False})

    async def test_capabilities_reports_news_true_when_granted(self):
        from app.api.agent_search import agent_search_capabilities
        await self._aufbauen(UserRole.MEMBER)
        async with self.Session() as db:
            out = await agent_search_capabilities({"agent_id": "a1"}, db)
        self.assertEqual(out, {"web": True, "news": True})

    async def test_a_fresh_install_without_a_key_is_closed(self):
        """Ohne Brave-Schluessel gibt es keinen Nachrichtenindex — auch fuer
        einen Nutzer, dem nichts verboten ist."""
        from app.api.agent_search import agent_search_capabilities
        await self._aufbauen(UserRole.MEMBER, mit_schluessel=False)
        async with self.Session() as db:
            out = await agent_search_capabilities({"agent_id": "a1"}, db)
        self.assertEqual(out["news"], False)


if __name__ == "__main__":
    unittest.main()
