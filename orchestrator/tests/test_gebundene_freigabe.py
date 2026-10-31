"""Freigaben an genau eine Handlung gebunden (core/gebundene_freigabe, P1-13/P2-17).

Vorher galt bei „Freigabe“: Der Agent fragt, der Mensch gibt frei, der Agent handelt
danach selbst — der Server sah nicht, ob es dieselbe Handlung war. Jetzt legt der
Server die Freigabe mit der genauen Handlung an und führt sie nach dem Freigeben
selbst aus, genau einmal.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api import approvals as api
from app.api import tickets
from app.core import gebundene_freigabe as gf
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.notification import Notification
from app.models.user import UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


def _admin():
    return SimpleNamespace(id="u1", role=UserRole.ADMIN, email="admin@example.test")


DATEN = {"title": "Drucker 3. OG", "description": "Druckt nicht", "priority": "hoch"}


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            from app.models.agent_access import AgentAccess
            for model in (Agent, AgentAccess, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            for aid, stand in (("frage", "ask"), ("frei", "allow"), ("nein", "deny")):
                db.add(Agent(id=aid, name=aid, state=AgentState.RUNNING, user_id="u1", config={},
                             autonomy_level="l3", access_policy={"autonomy_matrix": {"external_api": stand}}))
            await db.commit()
        self.verbindung = MagicMock(create_ticket=AsyncMock(return_value={"id": "T-7"}), add_comment=AsyncMock())
        self.p = patch.object(tickets, "_connector", AsyncMock(return_value=self.verbindung))
        self.p.start()
        self.redis = AsyncMock()
        self.redis.client = AsyncMock()

    async def asyncTearDown(self):
        self.p.stop()
        await self.engine.dispose()

    async def _freigabe(self, art="ticket_anlegen", daten=None, fingerabdruck=None) -> int:
        daten = dict(daten or DATEN)
        async with self.Session() as db:
            a = CommandApproval(agent_id="frage", command="ticket_create", description="x", risk_level="medium",
                                status=ApprovalStatus.PENDING,
                                meta={"kind": art, "input": {"daten": daten,
                                                             "fingerabdruck": fingerabdruck or gf.fingerabdruck(art, daten)}})
            db.add(a)
            await db.commit()
            return a.id

    async def _freigeben(self, aid: int):
        async with self.Session() as db:
            with patch.object(api, "_get_redis", return_value=self.redis):
                return await api.approve_request(str(aid), body=None, user=_admin(), db=db)

    async def _status(self, aid: int):
        async with self.Session() as db:
            return (await db.get(CommandApproval, aid)).status


class AnfrageTests(_Basis):

    async def test_freigabe_heisst_nicht_ausfuehren_sondern_anfragen(self):
        erfasst = {}

        async def anlegen(db, agent_id, body):
            erfasst.update(agent=agent_id, body=body)
            return {"approval_id": "42"}

        async with self.Session() as db:
            with patch.object(api, "anfrage_anlegen", side_effect=anlegen):
                antwort = await tickets.create_ticket(tickets.CreateTicket(**DATEN), db=db, _auth={"agent_id": "frage"})
        self.assertEqual(antwort.status_code, 202)
        self.verbindung.create_ticket.assert_not_called()
        body = erfasst["body"]
        self.assertEqual(body.kind, "ticket_anlegen")
        self.assertEqual(body.input["daten"], DATEN)
        self.assertEqual(body.input["fingerabdruck"], gf.fingerabdruck("ticket_anlegen", DATEN))
        self.assertEqual(body.view["name"], "handlung")
        self.assertIn("Drucker 3. OG", str(body.view["data"]))

    async def test_erlaubt_handelt_direkt_verboten_gar_nicht(self):
        async with self.Session() as db:
            await tickets.create_ticket(tickets.CreateTicket(**DATEN), db=db, _auth={"agent_id": "frei"})
            with self.assertRaises(HTTPException):
                await tickets.create_ticket(tickets.CreateTicket(**DATEN), db=db, _auth={"agent_id": "nein"})
        self.verbindung.create_ticket.assert_called_once()

    async def test_agent_kann_gebundene_art_nicht_selbst_anlegen(self):
        """Sonst stellte er eine Handlung nach seinen Wünschen zur Ausführung durch den Server."""
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await api.request_approval(api.ApprovalRequest(kind="ticket_anlegen", input={"daten": DATEN}),
                                           agent_auth={"agent_id": "frage"}, db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_zu_lang_wird_nicht_gekuerzt_sondern_abgelehnt(self):
        """Freigegeben würde sonst ein Text, den der Mensch nur zum Teil gesehen hat."""
        anlegen = AsyncMock(return_value={"approval_id": "1"})
        async with self.Session() as db:
            with patch.object(api, "anfrage_anlegen", anlegen):
                with self.assertRaises(HTTPException) as ctx:
                    await tickets.create_ticket(tickets.CreateTicket(title="x", description="A" * 9000),
                                                db=db, _auth={"agent_id": "frage"})
        self.assertEqual(ctx.exception.status_code, 413)
        anlegen.assert_not_called()

    async def test_umlaute_zaehlen_wie_in_der_freigabe(self):
        """Die Freigabe misst mit \\u-Escapes — 3000 „ü“ sind dort 18000 Zeichen."""
        async with self.Session() as db:
            with patch.object(api, "anfrage_anlegen", AsyncMock(return_value={"approval_id": "1"})):
                with self.assertRaises(HTTPException):
                    await tickets.create_ticket(tickets.CreateTicket(title="x", description="ü" * 3000),
                                                db=db, _auth={"agent_id": "frage"})

    async def test_ansicht_zeigt_den_vollen_text(self):
        erfasst = {}

        async def anlegen(db, agent_id, body):
            erfasst["body"] = body
            return {"approval_id": "1"}

        text = "B" * 5000
        async with self.Session() as db:
            with patch.object(api, "anfrage_anlegen", side_effect=anlegen):
                await tickets.create_ticket(tickets.CreateTicket(title="x", description=text), db=db,
                                            _auth={"agent_id": "frage"})
        self.assertIn(text, str(erfasst["body"].view["data"]))

    def test_ansicht_handlung_ist_erlaubt(self):
        self.assertIn("handlung", api.ERLAUBTE_ANSICHTEN)


class AusfuehrungTests(_Basis):

    async def test_freigeben_fuehrt_genau_diese_handlung_einmal_aus(self):
        aid = await self._freigabe()
        antwort = await self._freigeben(aid)
        self.verbindung.create_ticket.assert_called_once_with(
            title="Drucker 3. OG", description="Druckt nicht", priority="hoch")
        self.assertIn("T-7", antwort["applied"])
        self.assertEqual(await self._status(aid), ApprovalStatus.APPROVED)
        with self.assertRaises(HTTPException):
            await self._freigeben(aid)
        self.verbindung.create_ticket.assert_called_once()

    async def test_veraenderte_handlung_wird_nicht_ausgefuehrt(self):
        aid = await self._freigabe(fingerabdruck=gf.fingerabdruck("ticket_anlegen", {**DATEN, "title": "anders"}))
        with self.assertRaises(HTTPException) as ctx:
            await self._freigeben(aid)
        self.assertEqual(ctx.exception.status_code, 409)
        self.verbindung.create_ticket.assert_not_called()
        self.assertEqual(await self._status(aid), ApprovalStatus.PENDING)

    async def test_gescheiterte_ausfuehrung_laesst_die_freigabe_offen(self):
        self.verbindung.create_ticket.side_effect = RuntimeError("Ticketsystem antwortet nicht")
        aid = await self._freigabe()
        with self.assertRaises(HTTPException) as ctx:
            await self._freigeben(aid)
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertEqual(await self._status(aid), ApprovalStatus.PENDING)

    async def test_kommentar(self):
        aid = await self._freigabe("ticket_kommentar", {"ticket_id": "T-1", "text": "Erledigt"})
        await self._freigeben(aid)
        self.verbindung.add_comment.assert_called_once_with("T-1", "Erledigt")

    async def test_gewoehnliche_rueckfrage_bleibt_unberuehrt(self):
        async with self.Session() as db:
            a = CommandApproval(agent_id="frage", command="ask", description="Frage?", risk_level="low",
                                status=ApprovalStatus.PENDING, meta={"question": "Frage?"})
            db.add(a)
            await db.commit()
        antwort = await self._freigeben(a.id)
        self.assertIsNone(antwort["applied"])


if __name__ == "__main__":
    unittest.main()
