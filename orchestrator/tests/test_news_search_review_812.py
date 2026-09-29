"""Nachrichtenindex: die Punkte aus dem Re-Review zu #812.

K1  Ein SerpApi-Schluessel darf nie an Brave gehen.
K2  Die Freigabe gilt an JEDEM Zugang zum Nachrichtenindex — auch ueber die
    allgemeine Websuche mit Provider ``brave_news`` und ueber die Sprachfront.
K4  Ein Agent ohne Besitzer ist nicht automatisch ein Plattform-Agent.
Log Agent-/Nutzerkennungen werden vor dem Loggen bereinigt (CodeQL py/log-injection).
HTTP Die Routen ueber echtes HTTP mit echter Token-Pruefung: 401 ohne, mit
    falschem und mit fremdem Token; 403 ohne Freigabe; der Header entscheidet,
    nicht ein eingeschleustes ``agent_id`` im Body.

Alle Anbieter-Aufrufe sind abgefangen — kein echter Schluessel, kein Netz.
"""

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import get_db
from app.dependencies import make_agent_token
from app.models.agent import Agent
from app.models.platform_settings import PlatformSettings
from app.models.user import User, UserRole
from app.services.settings_service import SettingsService

BRAVE_WEB = "https://api.search.brave.com/res/v1/web/search"


def _anbieter(payload=None):
    """Abgefangener httpx-Client fuer die Anbieter-Aufrufe (Brave/SerpApi)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload if payload is not None else {"results": [], "web": {"results": []}}
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for modell in (PlatformSettings, Agent, User):
                await conn.run_sync(modell.metadata.create_all, tables=[modell.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def einstellungen(self, **werte):
        async with self.Session() as db:
            svc = SettingsService(db)
            for k, v in werte.items():
                await svc.set(k, v)
            await db.commit()

    async def nutzer(self, uid, rolle):
        async with self.Session() as db:
            db.add(User(id=uid, email=f"{uid}@example.test", name=uid, role=rolle))
            await db.commit()

    async def agent(self, aid, user_id=None, plattform=False):
        async with self.Session() as db:
            db.add(Agent(id=aid, name=aid, user_id=user_id, config={}, is_platform_agent=plattform))
            await db.commit()


class K1SchluesselNurFuerBraveTests(_MitDatenbank):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.nutzer("u1", UserRole.MEMBER)
        await self.agent("a1", "u1")

    async def test_a_serpapi_key_never_reaches_brave(self):
        from app.core.web_search import news_search_with_settings
        await self.einstellungen(web_search_provider="serp", web_search_api_key="nur-fuer-serpapi")
        with patch("httpx.AsyncClient") as mocked:
            async with self.Session() as db:
                out = await news_search_with_settings("gold", 5, db)
        self.assertEqual(out, [])
        mocked.assert_not_called()

    async def test_capabilities_do_not_offer_news_with_a_serpapi_key(self):
        """Die Anzeige nutzt dieselbe Aufloesung wie die Suche — kein
        irrefuehrendes news=true."""
        from app.api.agent_search import agent_search_capabilities
        await self.einstellungen(web_search_provider="serp", web_search_api_key="nur-fuer-serpapi")
        async with self.Session() as db:
            out = await agent_search_capabilities({"agent_id": "a1"}, db)
        self.assertFalse(out["news"])

    async def test_a_leftover_key_with_duckduckgo_is_not_used(self):
        """Wer von SerpApi auf DuckDuckGo wechselt, laesst den alten Schluessel stehen."""
        from app.core.web_search import news_search_with_settings
        await self.einstellungen(web_search_provider="duckduckgo", web_search_api_key="alt")
        with patch("httpx.AsyncClient") as mocked:
            async with self.Session() as db:
                self.assertEqual(await news_search_with_settings("gold", 5, db), [])
        mocked.assert_not_called()

    async def test_brave_news_provider_serves_news(self):
        from app.api.agent_search import agent_search_capabilities
        await self.einstellungen(web_search_provider="brave_news", web_search_api_key="bk")
        async with self.Session() as db:
            self.assertTrue((await agent_search_capabilities({"agent_id": "a1"}, db))["news"])


class K2AlleZugaengeTests(_MitDatenbank):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.einstellungen(web_search_provider="brave_news", web_search_api_key="bk")
        await self.nutzer("viewer", UserRole.VIEWER)
        await self.nutzer("member", UserRole.MEMBER)
        await self.agent("a-viewer", "viewer")
        await self.agent("a-member", "member")

    async def _web(self, agent_id):
        from app.api.agent_search import AgentWebSearchRequest, agent_web_search
        ctx, client = _anbieter()
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await agent_web_search(AgentWebSearchRequest(query="gold"), {"agent_id": agent_id}, db)
        return client.get.call_args.args[0]

    async def test_web_search_without_grant_uses_the_web_index(self):
        """Der gemeldete Umweg: /news 403, /web lieferte trotzdem Meldungen."""
        url = await self._web("a-viewer")
        self.assertEqual(url, BRAVE_WEB)

    async def test_web_search_with_grant_keeps_the_news_index(self):
        url = await self._web("a-member")
        self.assertIn("news/search", url)

    async def test_default_of_the_helper_is_closed(self):
        """Wer die Freigabe nicht prueft, bekommt den Nachrichtenindex nicht."""
        from app.core.web_search import web_search_with_settings
        ctx, client = _anbieter()
        async with self.Session() as db:
            with patch("httpx.AsyncClient", return_value=ctx):
                await web_search_with_settings("gold", 5, db)
        self.assertEqual(client.get.call_args.args[0], BRAVE_WEB)

    async def _stimme(self, agent_id):
        from app.services.realtime_voice_session import RealtimeVoiceSession
        v = RealtimeVoiceSession.__new__(RealtimeVoiceSession)
        v.agent_id = agent_id
        v._emit = AsyncMock()
        ctx, client = _anbieter({"results": [], "web": {"results": []}})
        with patch("app.db.session.async_session_factory", self.Session), \
             patch("httpx.AsyncClient", return_value=ctx):
            await v._web_search("gold", 5)
        return client.get.call_args.args[0]

    async def test_voice_search_without_grant_uses_the_web_index(self):
        self.assertEqual(await self._stimme("a-viewer"), BRAVE_WEB)

    async def test_voice_search_with_grant_keeps_the_news_index(self):
        self.assertIn("news/search", await self._stimme("a-member"))


class K4HerrenloseAgentenTests(_MitDatenbank):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.einstellungen(web_search_provider="brave", web_search_api_key="bk")

    async def test_an_ownerless_agent_is_refused(self):
        from app.core.search_access import agent_may_use_index
        await self.agent("verwaist", None, plattform=False)
        async with self.Session() as db:
            self.assertFalse(await agent_may_use_index("verwaist", "news", db))

    async def test_an_explicit_platform_agent_is_allowed(self):
        from app.core.search_access import agent_may_use_index
        await self.agent("plattform", None, plattform=True)
        async with self.Session() as db:
            self.assertTrue(await agent_may_use_index("plattform", "news", db))


class LogBereinigungTests(_MitDatenbank):
    async def test_a_stored_id_with_a_line_break_cannot_forge_a_log_line(self):
        from app.core.search_access import agent_may_use_index
        await self.agent("a1", "absent\nFORGED-LOG-LINE")
        with self.assertLogs("app.core.search_access", level=logging.WARNING) as logs:
            async with self.Session() as db:
                self.assertFalse(await agent_may_use_index("a1", "news", db))
        text = "\n".join(r.getMessage() for r in logs.records)
        self.assertNotIn("\n", text)
        self.assertIn("FORGED-LOG-LINE", text)  # der Inhalt bleibt lesbar, nur ohne Umbruch


class HttpMitEchterTokenPruefungTests(_MitDatenbank):
    """Route-Level: echte Anfragen durch FastAPI, ``verify_agent_token`` unveraendert."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.einstellungen(web_search_provider="brave", web_search_api_key="bk")
        await self.nutzer("viewer", UserRole.VIEWER)
        await self.nutzer("member", UserRole.MEMBER)
        await self.agent("a-viewer", "viewer")
        await self.agent("a-member", "member")

        from app.api import agent_search

        app = FastAPI()
        app.include_router(agent_search.router)

        async def _db():
            async with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = _db
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.http.aclose()
        await super().asyncTearDown()

    @staticmethod
    def _kopf(agent_id, token=None):
        return {"X-Agent-ID": agent_id, "Authorization": f"Bearer {token or make_agent_token(agent_id)}"}

    async def _news(self, headers, body=None):
        ctx, client = _anbieter()
        with patch("app.core.web_search.httpx.AsyncClient", return_value=ctx):
            r = await self.http.post("/agent-search/news", json=body or {"query": "gold"}, headers=headers)
        return r, client

    async def test_no_token_is_401(self):
        r, client = await self._news({})
        self.assertEqual(r.status_code, 401)
        client.get.assert_not_called()

    async def test_a_wrong_token_is_401(self):
        r, _ = await self._news(self._kopf("a-member", token="falsch"))
        self.assertEqual(r.status_code, 401)

    async def test_another_agents_token_is_401(self):
        """Token von a-viewer, ausgegeben als a-member."""
        r, _ = await self._news(self._kopf("a-member", token=make_agent_token("a-viewer")))
        self.assertEqual(r.status_code, 401)

    async def test_not_granted_is_403(self):
        r, client = await self._news(self._kopf("a-viewer"))
        self.assertEqual(r.status_code, 403)
        client.get.assert_not_called()

    async def test_granted_is_200(self):
        r, client = await self._news(self._kopf("a-member"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("news/search", client.get.call_args.args[0])

    async def test_an_injected_agent_id_in_the_body_does_not_change_who_asks(self):
        """Header a-viewer (gueltig), Body behauptet a-member: es gilt der Header."""
        r, _ = await self._news(self._kopf("a-viewer"), body={"query": "gold", "agent_id": "a-member"})
        self.assertEqual(r.status_code, 403)

    async def test_capabilities_over_http(self):
        r = await self.http.get("/agent-search/capabilities", headers=self._kopf("a-member"))
        self.assertEqual((r.status_code, r.json()), (200, {"web": True, "news": True}))
        r = await self.http.get("/agent-search/capabilities", headers=self._kopf("a-viewer"))
        self.assertEqual((r.status_code, r.json()), (200, {"web": True, "news": False}))
        r = await self.http.get("/agent-search/capabilities")
        self.assertEqual(r.status_code, 401)

    async def test_web_route_requires_a_token(self):
        r = await self.http.post("/agent-search/web", json={"query": "gold"})
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
