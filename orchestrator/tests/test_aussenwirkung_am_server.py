"""Wirkung nach außen entscheidet der Server (Welle 2 aus dem Bedrohungsmodell).

Die Rechte-Matrix steuerte bis hierhin vor allem den Prompt und den Hook im
Container. Der Hook läuft im Vertrauensbereich des Agenten, die Zugangsdaten für
Telegram, Ticketsystem und Microsoft liegen am Server — also prüft der Server.

Gefunden in der Bestandsaufnahme vom 30.10.:
* Telegram: jeder Sende-Endpunkt nahm jede beliebige ``chat_id``, das Profil des
  Bots war änderbar, keine Matrix-Prüfung.
* Microsoft: „Lesen + Schreiben“ sendete sofort, auch wenn die Matrix für
  „E-Mail / M365 senden“ „Freigabe“ oder „Verboten“ sagte (zwei Schalter).
* Ticketsystem: Anlegen und Kommentieren ohne Matrix-Prüfung.
"""

import ast
import inspect
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import faehigkeiten
from app.models.agent import Agent
from app.models.user import User, UserRole


def _agent(aid: str, matrix: dict | None = None, **config) -> Agent:
    return Agent(id=aid, name=aid, user_id="u1", autonomy_level="l3",
                 config={"integrations": ["microsoft"], **config},
                 access_policy={"autonomy_matrix": matrix} if matrix else None)


class _Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[User.__table__, Agent.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="u1", email="u@example.invalid", name="U", role=UserRole.MEMBER),
                _agent("frei", {"messaging": "allow", "email_m365": "allow", "external_api": "allow"},
                       msgraph_access="write"),
                _agent("frage", {"messaging": "ask", "email_m365": "ask", "external_api": "ask"},
                       msgraph_access="write"),
                _agent("nein", {"messaging": "deny", "email_m365": "deny", "external_api": "deny"},
                       msgraph_access="write"),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()


class VerlangenTests(_Db):
    async def test_verboten_sperrt(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await faehigkeiten.verlangen(db, "nein", "messaging")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("Chat / Telegram senden", ctx.exception.detail)

    async def test_freigabe_reicht_ausser_wenn_nur_erlaubt_zaehlt(self):
        async with self.Session() as db:
            self.assertEqual(await faehigkeiten.verlangen(db, "frage", "messaging"), "ask")
            with self.assertRaises(HTTPException):
                await faehigkeiten.verlangen(db, "frage", "messaging", nur_erlaubt=True)
            self.assertEqual(await faehigkeiten.verlangen(db, "frei", "messaging", nur_erlaubt=True), "allow")


class _FakeRedis:
    def __init__(self, auth=(), weiche=None):
        self.auth, self.weiche = set(auth), weiche

    async def sismember(self, _key, wert):
        return wert in self.auth

    async def get(self, _key):
        return self.weiche

    async def aclose(self):
        pass


class TelegramTests(_Db):

    async def _token(self, agent: str, chat: str, redis: _FakeRedis):
        from app.api import telegram_actions as ta
        async with self.Session() as db:
            with patch.object(ta.aioredis, "from_url", return_value=redis), \
                 patch.object(ta, "_bot_token_for_chat", AsyncMock(return_value="bot:token")):
                return await ta._token_fuer_chat(agent, chat, db)

    async def test_nur_chats_der_eigenen_nutzer(self):
        """Früher: jede beliebige chat_id — fremde Leute über den Bot anschreiben."""
        with self.assertRaises(HTTPException) as ctx:
            await self._token("frei", "999", _FakeRedis(auth={"111"}))
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(await self._token("frei", "111", _FakeRedis(auth={"111"})), "bot:token")

    async def test_ein_auf_den_agenten_geschalteter_chat_geht(self):
        self.assertEqual(await self._token("frei", "222", _FakeRedis(weiche="frei")), "bot:token")

    async def test_antworten_sind_ab_freigabe_frei_verboten_sperrt(self):
        self.assertEqual(await self._token("frage", "111", _FakeRedis(auth={"111"})), "bot:token")
        with self.assertRaises(HTTPException):
            await self._token("nein", "111", _FakeRedis(auth={"111"}))

    def test_jeder_sende_endpunkt_prueft_ziel_und_matrix(self):
        """Ein neuer Endpunkt mit chat_id, der nur _get_bot_token ruft, wäre wieder offen."""
        from app.api import telegram_actions as ta
        quelle = inspect.getsource(ta)
        baum = ast.parse(quelle)
        lesend = {"get_bot_info", "get_file", "get_commands", "get_description"}
        profil = {"set_commands", "delete_commands", "set_description"}
        offen = []
        for knoten in baum.body:
            if not isinstance(knoten, ast.AsyncFunctionDef):
                continue
            text = ast.get_source_segment(quelle, knoten)
            if "verify_agent_token" not in text or knoten.name in lesend:
                continue
            if knoten.name in profil:
                if "nur_erlaubt=True" not in text:
                    offen.append(knoten.name)
            elif "_token_fuer_chat(" not in text and "_darf_senden(" not in text:
                offen.append(knoten.name)
        self.assertEqual(offen, [])

    async def test_bot_profil_nur_bei_erlaubt(self):
        from app.api import telegram_actions as ta
        async with self.Session() as db:
            with self.assertRaises(HTTPException):
                await ta._darf_senden("frage", db, nur_erlaubt=True)
            await ta._darf_senden("frei", db, nur_erlaubt=True)


class MicrosoftMatrixTests(_Db):
    """„Lesen + Schreiben“ und die Matrix sind kein zweiter Schalter mehr."""

    async def _werkzeuge(self, agent: str) -> set[str]:
        from app.api import mcp_msgraph
        from app.dependencies import make_agent_token
        anfrage = SimpleNamespace(headers={"Authorization": f"Bearer {make_agent_token(agent)}"},
                                  json=AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
        with patch("app.api.mcp_msgraph.async_session_factory", self.Session), \
             patch("app.core.ms_access.read_only_enabled", return_value=False), \
             patch("app.core.sekretariat.ist_sekretariats_agent", AsyncMock(return_value=False)):
            antwort = await mcp_msgraph.mcp_msgraph_endpoint(agent, anfrage)
        return {t["name"] for t in json.loads(antwort.body)["result"]["tools"]}

    async def test_erlaubt_sendet_direkt(self):
        self.assertIn("ms_forward_email", await self._werkzeuge("frei"))

    async def test_freigabe_wird_zum_entwurf(self):
        namen = await self._werkzeuge("frage")
        self.assertIn("ms_send_email", namen, "Senden bleibt — als Entwurf mit Freigabe")
        self.assertNotIn("ms_forward_email", namen, "direktes Senden gibt es nicht")

    async def test_verboten_heisst_nur_lesen(self):
        namen = await self._werkzeuge("nein")
        self.assertNotIn("ms_send_email", namen)
        self.assertIn("ms_list_emails", namen)


class TicketTests(_Db):

    async def test_verboten_legt_kein_ticket_an(self):
        from app.api import tickets
        verbindung = MagicMock(create_ticket=AsyncMock(), add_comment=AsyncMock())
        async with self.Session() as db:
            with patch.object(tickets, "_connector", AsyncMock(return_value=verbindung)):
                with self.assertRaises(HTTPException):
                    await tickets.create_ticket(tickets.CreateTicket(title="x"), db=db, _auth={"agent_id": "nein"})
                with self.assertRaises(HTTPException):
                    await tickets.comment_ticket("T1", tickets.AddComment(text="x"), db=db, _auth={"agent_id": "nein"})
                await tickets.create_ticket(tickets.CreateTicket(title="x"), db=db, _auth={"agent_id": "frage"})
        verbindung.add_comment.assert_not_called()
        verbindung.create_ticket.assert_called_once()


if __name__ == "__main__":
    unittest.main()
