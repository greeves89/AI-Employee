"""Löschkonzept (#892): Nutzer und Agenten so löschen, dass nichts verwaist.

Zwei Befunde aus dem Markttest:

* ``DELETE /auth/users/{id}`` scheiterte mit HTTP 500, sobald der Nutzer Agenten
  besaß — ``agents.user_id`` verweist ohne ``ON DELETE`` auf ``users.id``. Jetzt:
  eine verständliche Rückfrage (409) mit der Liste der Agenten, und zwei Wege
  weiter: Agenten übertragen oder samt Daten löschen.
* „Agent löschen mit Daten“ entfernte nur Container und Volumes. Chats,
  Gesprächsfäden und Gedächtnis blieben in der Datenbank — die Suche fand die
  Gespräche des gelöschten Agenten weiter.

SQLite prüft Fremdschlüssel nur mit ``PRAGMA foreign_keys=ON`` — ohne das
bewiese der Nutzer-Test nichts.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import auth as auth_api
from app.core.agent_manager import AgentManager
from app.models.agent import Agent
from app.models.agent_access import AgentAccess
from app.models.agent_message import AgentMessage
from app.models.agent_plan_item import AgentPlanItem
from app.models.agent_template import AgentTemplate
from app.models.agent_todo import AgentTodo
from app.models.ai_account import AIAccount
from app.models.audit_log import AuditLog
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.custom_role import CustomRole
from app.models.memory import AgentMemory, AgentMemoryLink, AgentMemoryTag
from app.models.notification import Notification
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.task_rating import TaskRating
from app.models.user import User, UserRole

TABELLEN = [
    User.__table__, Agent.__table__, AgentAccess.__table__, AgentTemplate.__table__,
    Task.__table__, TaskRating.__table__, Schedule.__table__,
    ChatMessage.__table__, ChatSession.__table__, AgentMemory.__table__,
    AgentMemoryTag.__table__, AgentMemoryLink.__table__, AgentTodo.__table__,
    AgentMessage.__table__, AIAccount.__table__, CustomRole.__table__,
    AgentPlanItem.__table__, Notification.__table__, AuditLog.__table__,
]


def _docker():
    d = MagicMock()
    d.remove_container = MagicMock()
    d.remove_volume = MagicMock()
    return d


def _redis():
    r = MagicMock()
    r.client = None  # _publish_event überspringt dann still
    return r


def _anfrage():
    """Ein Request-Ersatz: App-Zustand mit Docker/Redis, Anmeldung über Patch."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(docker=_docker(), redis=_redis())))


class _Basis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=TABELLEN)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(User(id="admin", email="admin@example.invalid", name="Admin",
                        password_hash="x", role=UserRole.ADMIN))
            db.add(User(id="u1", email="u1@example.invalid", name="Nutzer",
                        password_hash="x", role=UserRole.MEMBER))
            await db.flush()
            db.add(Agent(id="ag1", name="Recherche", user_id="u1", volume_name="workspace-ag1",
                         config={"session_volume": "claude-session-ag1"}))
            db.add(Agent(id="ag2", name="Fremd", user_id="admin"))
            await db.flush()
            db.add(AgentTemplate(name="vorlage-u1", display_name="Vorlage", created_by="u1"))
            db.add(AgentAccess(agent_id="ag2", user_id="admin", granted_by="u1"))
            for aid in ("ag1", "ag2"):
                db.add(ChatMessage(agent_id=aid, session_id="s", message_id=f"m-{aid}",
                                   role="user", content=f"Gespräch mit {aid}"))
                db.add(ChatSession(agent_id=aid, session_id="s", title="Faden"))
                db.add(AgentTodo(agent_id=aid, title="Offen"))
                db.add(AgentMessage(from_agent_id=aid, from_agent_name=aid,
                                    to_agent_id="x", text="Hallo"))
            await db.flush()
            m1 = AgentMemory(agent_id="ag1", category="fakt", key="k1", content="geheim")
            m2 = AgentMemory(agent_id="ag1", category="fakt", key="k2", content="auch")
            m3 = AgentMemory(agent_id="ag2", category="fakt", key="k3", content="bleibt")
            db.add_all([m1, m2, m3])
            await db.flush()
            db.add(AgentMemoryTag(memory_id=m1.id, tag="t"))
            db.add(AgentMemoryLink(source_id=m1.id, target_id=m2.id, relation="related"))
            db.add(AgentMemoryLink(source_id=m3.id, target_id=m1.id, relation="related"))
            await db.commit()
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _zaehle(self, modell, **filter_):
        async with self.Session() as db:
            stmt = select(modell)
            for feld, wert in filter_.items():
                stmt = stmt.where(getattr(modell, feld) == wert)
            return len((await db.execute(stmt)).scalars().all())


class AgentMitDatenLoeschenTests(_Basis):
    async def _entferne(self, remove_data):
        async with self.Session() as db:
            await AgentManager(db, _docker(), _redis()).remove_agent("ag1", remove_data=remove_data)

    async def test_mit_daten_hinterlaesst_keine_chats_und_kein_gedaechtnis(self):
        await self._entferne(True)
        self.assertEqual(await self._zaehle(ChatMessage, agent_id="ag1"), 0)
        self.assertEqual(await self._zaehle(ChatSession, agent_id="ag1"), 0)
        self.assertEqual(await self._zaehle(AgentMemory, agent_id="ag1"), 0)
        self.assertEqual(await self._zaehle(AgentTodo, agent_id="ag1"), 0)
        self.assertEqual(await self._zaehle(AgentMessage, from_agent_id="ag1"), 0)
        self.assertEqual(await self._zaehle(AgentMemoryTag), 0)
        # Verknüpfungen, die auf ein gelöschtes Gedächtnis zeigen, verschwinden mit.
        self.assertEqual(await self._zaehle(AgentMemoryLink), 0)

    async def test_mit_daten_laesst_andere_agenten_unberuehrt(self):
        await self._entferne(True)
        self.assertEqual(await self._zaehle(ChatMessage, agent_id="ag2"), 1)
        self.assertEqual(await self._zaehle(ChatSession, agent_id="ag2"), 1)
        self.assertEqual(await self._zaehle(AgentMemory, agent_id="ag2"), 1)
        self.assertEqual(await self._zaehle(AgentTodo, agent_id="ag2"), 1)

    async def test_ohne_daten_bleiben_die_gespraeche_erhalten(self):
        await self._entferne(False)
        self.assertEqual(await self._zaehle(ChatMessage, agent_id="ag1"), 1)
        self.assertEqual(await self._zaehle(AgentMemory, agent_id="ag1"), 2)


class NutzerLoeschenTests(_Basis):
    async def _loesche(self, agenten=None, an=None):
        async with self.Session() as db:
            admin = await db.get(User, "admin")
            with patch("app.dependencies.get_current_user", AsyncMock(return_value=admin)):
                return await auth_api.delete_user("u1", _anfrage(), agenten=agenten, an=an, db=db)

    async def test_mit_agenten_kommt_eine_rueckfrage_statt_500(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._loesche()
        self.assertEqual(ctx.exception.status_code, 409)
        detail = ctx.exception.detail
        self.assertEqual(detail["code"], "nutzer_hat_agenten")
        self.assertEqual([a["id"] for a in detail["agenten"]], ["ag1"])
        # Nichts gelöscht.
        self.assertEqual(await self._zaehle(User, id="u1"), 1)
        self.assertEqual(await self._zaehle(Agent, id="ag1"), 1)

    async def test_agenten_loeschen_entfernt_nutzer_agenten_und_ihre_daten(self):
        ergebnis = await self._loesche(agenten="loeschen")
        self.assertTrue(ergebnis["ok"])
        self.assertEqual(await self._zaehle(User, id="u1"), 0)
        self.assertEqual(await self._zaehle(Agent, id="ag1"), 0)
        self.assertEqual(await self._zaehle(ChatMessage, agent_id="ag1"), 0)
        self.assertEqual(await self._zaehle(Agent, id="ag2"), 1)

    async def test_agenten_uebertragen_haengt_sie_an_den_admin(self):
        await self._loesche(agenten="uebertragen")
        self.assertEqual(await self._zaehle(User, id="u1"), 0)
        async with self.Session() as db:
            agent = await db.get(Agent, "ag1")
        self.assertEqual(agent.user_id, "admin")
        self.assertEqual(await self._zaehle(ChatMessage, agent_id="ag1"), 1)

    async def test_uebertragen_an_unbekannten_nutzer_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._loesche(agenten="uebertragen", an="gibt-es-nicht")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(await self._zaehle(User, id="u1"), 1)

    async def test_unbekannte_option_wird_abgelehnt(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._loesche(agenten="irgendwas")
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_verweise_aus_vorlagen_und_freigaben_blockieren_nicht(self):
        """Auch ohne eigene Agenten scheiterte das Löschen an Fremdschlüsseln."""
        async with self.Session() as db:
            await db.delete(await db.get(Agent, "ag1"))
            await db.commit()
        ergebnis = await self._loesche()
        self.assertTrue(ergebnis["ok"])
        async with self.Session() as db:
            vorlage = (await db.execute(select(AgentTemplate))).scalars().one()
            freigabe = (await db.execute(select(AgentAccess))).scalars().one()
        self.assertIsNone(vorlage.created_by)
        self.assertIsNone(freigabe.granted_by)


if __name__ == "__main__":
    unittest.main()
