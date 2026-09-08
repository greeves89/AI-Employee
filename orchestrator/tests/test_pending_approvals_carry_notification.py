"""Wartende Freigaben tragen ihre Meldungs-ID — sonst bleibt die App stumm.

Gemeldet aus dem Betrieb: "Wenn ein Agent auf eine Freigabe wartet, sehe ich
das nur auf dem Desktop, in der App im Chat nicht." Die Freigabe-Karte im Chat
der mobilen App antwortet ueber die Meldungs-ID; die Liste der wartenden
Freigaben lieferte sie nicht. Also konnte die App beim Oeffnen eines Chats keine
Karte zeigen — nur live, waehrend der Chat gerade offen war.

Geprueft wird das Verhalten mit echten Zeilen in einer Datenbank, nicht der
Wortlaut des Codes: Fragen stellen, Liste holen, ID muss dranhaengen — und die
Mandantentrennung der Liste darf sich dabei nicht aendern.
"""

import unittest
from types import SimpleNamespace

from app.api import approvals as api
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.command_approval import CommandApproval
from app.models.notification import Notification
from app.models.user import UserRole
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):
    return "JSON"


def _member(uid="u2"):
    return SimpleNamespace(id=uid, role=UserRole.MEMBER, email=f"{uid}@example.test")


class MeldungsIdTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Eigener", state=AgentState.RUNNING, user_id="u2", config={}))
            db.add(Agent(id="a2", name="Fremder", state=AgentState.RUNNING, user_id="u9", config={}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _ask(self, db, agent_id, frage):
        return await api.request_approval(
            api.ApprovalRequest(question=frage, reasoning="Test"),
            agent_auth={"agent_id": agent_id}, db=db,
        )

    async def test_jede_wartende_freigabe_traegt_ihre_meldung(self):
        async with self.Session() as db:
            await self._ask(db, "a1", "Darf ich A?")
            await self._ask(db, "a1", "Darf ich B?")
            liste = await api.list_pending_approvals(user=_member("u2"), db=db)
        self.assertEqual(2, liste["count"])
        for e in liste["approvals"]:
            with self.subTest(frage=e["question"]):
                self.assertTrue(e.get("notification_id"), "Meldungs-ID fehlt — die App kann keine Karte zeigen")

    async def test_die_id_gehoert_zur_richtigen_freigabe(self):
        """Zwei Freigaben, zwei Meldungen — keine Verwechslung."""
        async with self.Session() as db:
            await self._ask(db, "a1", "Erste")
            await self._ask(db, "a1", "Zweite")
            liste = await api.list_pending_approvals(user=_member("u2"), db=db)
            from sqlalchemy import select
            for e in liste["approvals"]:
                n = (await db.execute(select(Notification).where(Notification.id == int(e["notification_id"])))).scalar_one()
                self.assertEqual(e["approval_id"], str((n.meta or {}).get("approval_id")))

    async def test_mandantentrennung_bleibt(self):
        """Der fremde Agent darf durch die Erweiterung nicht sichtbar werden."""
        async with self.Session() as db:
            await self._ask(db, "a1", "Meins")
            await self._ask(db, "a2", "Fremd")
            liste = await api.list_pending_approvals(user=_member("u2"), db=db)
        self.assertEqual(["Meins"], [e["question"] for e in liste["approvals"]])

    async def test_leere_liste_bleibt_leer(self):
        async with self.Session() as db:
            liste = await api.list_pending_approvals(user=_member("u2"), db=db)
        self.assertEqual({"approvals": [], "count": 0}, liste)


if __name__ == "__main__":
    unittest.main()
