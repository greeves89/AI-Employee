"""Freigaben-Verlauf mit Entscheider (#897).

Befund im Markttest: die Freigaben-Seite zeigte nur Offenes — was erledigt oder
abgelehnt war, und von wem, ließ sich nirgends nachsehen. Die Regeln-Liste
zeigte acht gleiche „Auto-Preset“-Zeilen ohne Agentennamen.
"""

import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api import approval_rules as rules_api
from app.api import approvals as api
from app.models.agent import Agent, AgentState
from app.models.approval_rule import ApprovalRule
from app.models.audit_log import AuditLog
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.custom_role import CustomRole
from app.models.notification import Notification
from app.models.user import User, UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _admin():
    return SimpleNamespace(id="u1", role=UserRole.ADMIN, email="chefin@example.com")


def _member(uid="u2"):
    return SimpleNamespace(id=uid, role=UserRole.MEMBER, email=f"{uid}@example.com")


class FreigabenVerlauf(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (CustomRole, User, Agent, CommandApproval, Notification, AuditLog, ApprovalRule):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(User(id="u1", email="chefin@example.com", name="Chefin", role=UserRole.ADMIN))
            db.add(User(id="u2", email="u2@example.com", name="Mitglied", role=UserRole.MEMBER))
            db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING, user_id="u2", config={}))
            db.add(Agent(id="a2", name="Fremd", state=AgentState.RUNNING, user_id="u9", config={}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _befehl(self, db, agent_id="a1", cmd="ls"):
        res = await api.request_approval(
            api.ApprovalRequest(tool="bash", input={"command": cmd}, reasoning=cmd),
            agent_auth={"agent_id": agent_id}, db=db,
        )
        return res["approval_id"]

    async def _verlauf(self, db, user, **kw):
        params = dict(status=None, agent_id=None, von=None, bis=None, offset=0, limit=50)
        params.update(kw)
        return await api.approval_history(**params, user=user, db=db)

    async def test_approve_setzt_entscheider(self):
        async with self.Session() as db:
            aid = await self._befehl(db)
            await api.approve_request(aid, None, user=_member(), db=db)
            zeile = await db.get(CommandApproval, int(aid))
            self.assertEqual(zeile.resolved_by_user_id, "u2")

    async def test_abgelehnte_freigabe_im_verlauf(self):
        async with self.Session() as db:
            aid = await self._befehl(db)
            await api.deny_request(aid, api.ApprovalDecision(decision="deny", reason="nein"),
                                   user=_member(), db=db)
            res = await self._verlauf(db, _member())
            [eintrag] = res["approvals"]
            self.assertEqual(eintrag["status"], "denied")
            self.assertEqual(eintrag["agent_name"], "Buchhaltung")
            self.assertEqual(eintrag["resolved_by_name"], "Mitglied")
            self.assertIsNotNone(eintrag["resolved_at"])

    async def test_offenes_steht_nicht_im_verlauf(self):
        async with self.Session() as db:
            await self._befehl(db)
            self.assertEqual((await self._verlauf(db, _admin()))["total"], 0)

    async def test_mitglied_sieht_keine_fremden_freigaben(self):
        async with self.Session() as db:
            eigen = await self._befehl(db, "a1")
            fremd = await self._befehl(db, "a2", cmd="pwd")
            await api.approve_request(eigen, None, user=_admin(), db=db)
            await api.approve_request(fremd, None, user=_admin(), db=db)

            mitglied = await self._verlauf(db, _member())
            self.assertEqual([a["approval_id"] for a in mitglied["approvals"]], [eigen])
            # Auch nicht über den Agentenfilter.
            gezielt = await self._verlauf(db, _member(), agent_id="a2")
            self.assertEqual(gezielt["approvals"], [])
            self.assertEqual((await self._verlauf(db, _admin()))["total"], 2)

    async def test_filter_status(self):
        async with self.Session() as db:
            a = await self._befehl(db, cmd="eins")
            b = await self._befehl(db, cmd="zwei")
            await api.approve_request(a, None, user=_admin(), db=db)
            await api.deny_request(b, api.ApprovalDecision(decision="deny"), user=_admin(), db=db)
            res = await self._verlauf(db, _admin(), status="denied")
            self.assertEqual([x["approval_id"] for x in res["approvals"]], [b])
            with self.assertRaises(HTTPException):
                await self._verlauf(db, _admin(), status="pending")

    async def test_filter_zeitraum(self):
        async with self.Session() as db:
            alt = CommandApproval(
                agent_id="a1", command="bash", description="alt", status=ApprovalStatus.APPROVED,
                created_at=datetime.now(timezone.utc) - timedelta(days=10),
                resolved_at=datetime.now(timezone.utc) - timedelta(days=10), meta={},
            )
            db.add(alt)
            await db.commit()
            neu = await self._befehl(db)
            await api.approve_request(neu, None, user=_admin(), db=db)
            heute = date.today()
            res = await self._verlauf(db, _admin(), von=heute - timedelta(days=1), bis=heute)
            self.assertEqual([x["approval_id"] for x in res["approvals"]], [neu])


class RegelKopienMitAgentenname(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, ApprovalRule):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_regel_kopie_traegt_agentennamen(self):
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING, user_id="u2", config={}))
            db.add(ApprovalRule(name="Dateien lesen", description="d", category="file_read",
                                agent_id="a1", created_by="system:autonomy"))
            db.add(ApprovalRule(name="Global", description="d", category="custom"))
            await db.commit()
            res = await rules_api.list_rules(user=_admin(), db=db)
            namen = {r["name"]: r for r in res["rules"]}
            self.assertTrue(namen["Dateien lesen"]["is_preset"])
            self.assertEqual(namen["Dateien lesen"]["agent_name"], "Buchhaltung")
            self.assertIsNone(namen["Global"]["agent_name"])


if __name__ == "__main__":
    unittest.main()
