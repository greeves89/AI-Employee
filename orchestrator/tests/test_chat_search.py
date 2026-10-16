"""Werkzeug ``chat_search``: wörtliche Suche im laufenden Gespräch.

Über echtes HTTP mit echter Token-Prüfung (``require_auth_or_agent``):

Zugang     401 ohne Token, 403 für einen fremden Agenten und für Menschen.
Trennung   Gesucht wird NUR im übergebenen Gespräch — ``ChatMessage`` hat keinen
           Nutzer, ein Agent kann mehreren Menschen gehören.
Filter     Verdichtete und aus dem Kontext genommene Nachrichten fehlen wie im
           Verlauf für den Agenten; doppelte Zeilen zählen einmal.
Anfrage    Mehrere Wörter = alle müssen vorkommen, Groß-/Kleinschreibung egal,
           ``%``/``_`` sind Text; Länge 1..200, Treffer höchstens 30.
"""

import unittest
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.chat_history import ausschnitt, sichtbare_nachrichten
from app.db.session import get_db
from app.dependencies import make_agent_token
from app.models.chat_message import ChatMessage
from app.models.user import User, UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


class ChatSucheHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for modell in (ChatMessage, User):
                await conn.run_sync(modell.metadata.create_all, tables=[modell.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        # Ohne jeden Nutzer liefe die Anlage im Einrichtungsmodus (anonymer Admin).
        async with self.Session() as db:
            db.add(User(id="u1", email="u1@example.test", name="u1", role=UserRole.ADMIN))
            await db.commit()

        from app.api import agents

        app = FastAPI()
        app.include_router(agents.router)

        async def _db():
            async with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = _db
        self.app = app
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self._n = 0

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.engine.dispose()

    async def zeile(self, content, *, agent="a1", session="s1", role="user", meta=None,
                    message_id=None):
        self._n += 1
        async with self.Session() as db:
            db.add(ChatMessage(
                agent_id=agent, session_id=session, message_id=message_id or f"m{self._n}",
                role=role, content=content, meta=meta,
                timestamp=T0 + timedelta(minutes=self._n),
            ))
            await db.commit()

    @staticmethod
    def kopf(agent_id="a1", token=None):
        return {"X-Agent-ID": agent_id, "Authorization": f"Bearer {token or make_agent_token(agent_id)}"}

    async def suche(self, q, *, agent="a1", session="s1", headers=None, **params):
        return await self.http.get(
            f"/agents/{agent}/chat/search",
            params={"session_id": session, "q": q, **params},
            headers=self.kopf() if headers is None else headers,
        )

    # ── Zugang ─────────────────────────────────────────────────────────────

    async def test_ohne_token_401(self):
        await self.zeile("Die Rechnungsnummer ist 4711")
        r = await self.suche("4711", headers={})
        self.assertEqual(r.status_code, 401)

    async def test_falsches_token_401(self):
        r = await self.suche("4711", headers=self.kopf("a1", token="falsch"))
        self.assertEqual(r.status_code, 401)

    async def test_fremder_agent_kommt_nicht_an_fremdes_gespraech(self):
        await self.zeile("Geheimer Plan: Projekt Falke")
        r = await self.suche("Falke", headers=self.kopf("a2"))
        self.assertEqual(r.status_code, 403)

    async def test_ein_mensch_bekommt_die_suche_nicht(self):
        """Selbst ein Admin nicht: die Suche ist das Werkzeug des Agenten."""
        from types import SimpleNamespace

        from app.dependencies import require_auth_or_agent

        await self.zeile("Angebot Falke")
        self.app.dependency_overrides[require_auth_or_agent] = (
            lambda: SimpleNamespace(id="u1", role="admin", principal_type="user"))
        r = await self.suche("Falke", headers={})
        self.assertEqual(r.status_code, 403)

    # ── Trennung ───────────────────────────────────────────────────────────

    async def test_nur_das_uebergebene_gespraech_wird_durchsucht(self):
        await self.zeile("Angebot Falke im eigenen Gespraech", session="s1")
        await self.zeile("Angebot Falke im Gespraech eines anderen Menschen", session="s2")
        await self.zeile("Angebot Falke bei einem anderen Agenten", agent="a2", session="s1")
        r = await self.suche("falke")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual([m["excerpt"] for m in body["matches"]], ["Angebot Falke im eigenen Gespraech"])

    async def test_ohne_gespraech_keine_suche(self):
        r = await self.http.get("/agents/a1/chat/search", params={"q": "x"}, headers=self.kopf())
        self.assertEqual(r.status_code, 422)

    # ── Anfrage ────────────────────────────────────────────────────────────

    async def test_mehrere_woerter_muessen_alle_vorkommen(self):
        await self.zeile("Termin mit dem Lieferanten am Dienstag")
        await self.zeile("Termin am Mittwoch")
        await self.zeile("Der Lieferant hat abgesagt")
        r = await self.suche("TERMIN lieferanten")
        body = r.json()
        self.assertEqual(body["total"], 1)
        self.assertIn("Dienstag", body["matches"][0]["excerpt"])

    async def test_neueste_zuerst_mit_kennung_rolle_und_zeit(self):
        await self.zeile("Budget 100", message_id="alt")
        await self.zeile("Budget 200", role="assistant", message_id="neu")
        body = (await self.suche("budget")).json()
        self.assertEqual([m["message_id"] for m in body["matches"]], ["neu", "alt"])
        self.assertEqual(body["matches"][0]["role"], "assistant")
        self.assertTrue(body["matches"][0]["timestamp"].startswith("2026-10-01"))

    async def test_limit_begrenzt_treffer_nicht_die_gesamtzahl(self):
        for i in range(5):
            await self.zeile(f"Punkt {i} der Liste")
        body = (await self.suche("liste", limit=2)).json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(len(body["matches"]), 2)

    async def test_platzhalter_sind_text(self):
        await self.zeile("Rabatt 10% vereinbart")
        await self.zeile("Rabatt 10 Euro vereinbart")
        body = (await self.suche("10%")).json()
        self.assertEqual(body["total"], 1)
        body = (await self.suche("a_b")).json()
        self.assertEqual(body["total"], 0)

    async def test_anfrage_wird_geprueft(self):
        self.assertEqual((await self.suche("")).status_code, 422)
        self.assertEqual((await self.suche("x" * 201)).status_code, 422)
        self.assertEqual((await self.suche("   ")).status_code, 422)
        self.assertEqual((await self.suche("x", limit=31)).status_code, 422)
        self.assertEqual((await self.suche("x", limit=0)).status_code, 422)
        self.assertEqual((await self.suche("x" * 200)).status_code, 200)

    # ── Filter wie im Verlauf ──────────────────────────────────────────────

    async def test_verdichtete_und_ausgeschlossene_fehlen(self):
        await self.zeile("Kennwort-Hinweis alt", meta={"compacted": True})
        await self.zeile("Kennwort-Hinweis entfernt", meta={"context_excluded": True})
        await self.zeile("Kennwort-Hinweis sichtbar", meta={"tool_output_excluded": True})
        body = (await self.suche("kennwort-hinweis")).json()
        self.assertEqual([m["excerpt"] for m in body["matches"]], ["Kennwort-Hinweis sichtbar"])

    async def test_verlauf_fuer_den_agenten_filtert_genauso(self):
        """Dieselbe Regel (``sichtbare_nachrichten``) gilt im Verlauf."""
        await self.zeile("verdichtet", meta={"compacted": True})
        await self.zeile("bleibt")
        r = await self.http.get("/agents/a1/chat/history", params={"session_id": "s1"},
                                headers=self.kopf())
        self.assertEqual([m["content"] for m in r.json()["messages"]], ["bleibt"])


class AusschnittTests(unittest.TestCase):
    def test_kurzer_text_bleibt_ganz(self):
        self.assertEqual(ausschnitt("kurz", ["kurz"]), "kurz")

    def test_langer_text_wird_um_die_fundstelle_geschnitten(self):
        text = "a" * 1000 + " ZIEL " + "b" * 1000
        out = ausschnitt(text, ["ziel"])
        self.assertIn("ZIEL", out)
        self.assertTrue(out.startswith("…") and out.endswith("…"))
        self.assertLessEqual(len(out), 402)

    def test_fundstelle_am_ende(self):
        text = "a" * 1000 + " ZIEL"
        out = ausschnitt(text, ["ziel"])
        self.assertTrue(out.endswith("ZIEL"))


class SichtbareNachrichtenTests(unittest.TestCase):
    def test_dubletten_einmal_und_filter_nur_fuers_modell(self):
        def m(i, content, meta=None):
            return ChatMessage(id=i, message_id="x", role="user", content=content, meta=meta)

        rows = [m(1, "a"), m(2, "a"), m(3, "b", {"compacted": True})]
        self.assertEqual([r.id for r in sichtbare_nachrichten(rows, for_model=False)], [1, 3])
        self.assertEqual([r.id for r in sichtbare_nachrichten(rows, for_model=True)], [1])


if __name__ == "__main__":
    unittest.main()



def test_hoechstens_acht_suchwoerter():
    from app.core.chat_history import MAX_SUCHWOERTER, suchwoerter
    assert len(suchwoerter(" ".join(f"w{i}" for i in range(50)))) == MAX_SUCHWOERTER


def test_chat_loeschen_vergisst_den_faden_beim_agenten():
    """Sonst taucht eine alte Uebergabe in einem spaeteren Gespraech wieder auf."""
    import asyncio
    from types import SimpleNamespace

    from app.api.agents import _chat_faden_vergessen

    geloescht = []

    class _Client:
        async def delete(self, *schluessel):
            geloescht.extend(schluessel)

    asyncio.run(_chat_faden_vergessen(SimpleNamespace(client=_Client()), "a1", "s1"))
    assert "agent:a1:chat_uebergabe:webapp:s1" in geloescht
    assert "agent:a1:claude_session:ios:s1" in geloescht
    assert "agent:a1:chat_neuanfang:voice:s1" in geloescht
    geloescht.clear()
    asyncio.run(_chat_faden_vergessen(SimpleNamespace(client=_Client()), "a1", "telegram:7"))
    assert "agent:a1:chat_uebergabe:telegram:7" in geloescht
