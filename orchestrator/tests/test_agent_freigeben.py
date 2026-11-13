"""Agent zum Nutzen freigeben (AgentAccess.rolle == "nutzen").

Ein Agent, viele Menschen: Der Besitzer gibt ihn Kollegen frei. Die dürfen mit
ihm arbeiten (Chat, Sprache, Dateien), aber nicht einrichten. Standard jeder
Zugriffsprüfung ist VERWALTEN — eine neue Schnittstelle ist für Nur-Nutzer zu,
bis sie sich ausdrücklich als Nutzen ausweist. Das prüfen diese Tests über die
echten Funktionen.
"""

import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import agents as api
from app.core import ownership
from app.dependencies import require_agent_access
from app.models.agent import Agent
from app.models.agent_access import ROLLE_NUTZEN, AgentAccess
from app.models.audit_log import AuditLog
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.notification import Notification
from app.models.user import User, UserRole

BESITZER = SimpleNamespace(id="besitzer", role=UserRole.MEMBER)
KOLLEGE = SimpleNamespace(id="kollege", role=UserRole.MEMBER)
FREMD = SimpleNamespace(id="fremd", role=UserRole.MEMBER)
ALT = SimpleNamespace(id="alt", role=UserRole.MEMBER)
ADMIN = SimpleNamespace(id="adm", role=UserRole.ADMIN)


class FreigebenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for m in (User, Agent, AgentAccess, AuditLog, ChatMessage, ChatSession, Notification):
                await conn.run_sync(m.metadata.create_all, tables=[m.__table__])
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.db.add_all([
            User(id=u, email=f"{u}@example.com", name=u.title(), role=UserRole.MEMBER)
            for u in ("besitzer", "kollege", "fremd", "alt")
        ])
        self.db.add(User(id="weg", email="weg@example.com", name="Weg", role=UserRole.MEMBER, is_active=False))
        self.db.add(Agent(id="a1", name="Assistenz", user_id="besitzer", config={}))
        # Aeltere Freigabe ohne Rolle (Zuweisen/Verteilen): behaelt ihre Wirkung.
        self.db.add(AgentAccess(agent_id="a1", user_id="alt"))
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _freigeben(self, *ids, user=BESITZER):
        return await api.set_agent_freigaben("a1", api.FreigabenUpdate(nutzer_ids=list(ids)),
                                             user=user, db=self.db)

    # --- Freigeben ------------------------------------------------------------

    async def test_besitzer_gibt_frei_und_nimmt_zurueck(self):
        r = await self._freigeben("kollege")
        self.assertEqual([n["id"] for n in r["nutzer"]], ["kollege"])
        r = await self._freigeben()
        self.assertEqual(r["nutzer"], [])
        self.assertIsNotNone(await self.db.get(AgentAccess, ("a1", "alt")), "alte Freigabe bleibt")
        protokoll = (await self.db.execute(select(AuditLog))).scalars().all()
        self.assertEqual(len(protokoll), 2)

    async def test_nur_verwalter_duerfen_freigeben(self):
        await self._freigeben("kollege")
        for wer in (KOLLEGE, FREMD):
            with self.assertRaises(HTTPException) as e:
                await self._freigeben("fremd", user=wer)
            self.assertEqual(e.exception.status_code, 403)
        await self._freigeben("kollege", "fremd", user=ADMIN)

    async def test_unbekannte_oder_deaktivierte_personen_abgelehnt(self):
        for liste in (["gibtsnicht"], ["weg"]):
            with self.assertRaises(HTTPException) as e:
                await self._freigeben(*liste)
            self.assertEqual(e.exception.status_code, 422)
        r = await self._freigeben("besitzer", "kollege")
        self.assertEqual([n["id"] for n in r["nutzer"]], ["kollege"], "Besitzer nicht sich selbst")

    # --- Nutzen ja, Verwalten nein --------------------------------------------

    async def test_nutzen_ja_verwalten_nein(self):
        await self._freigeben("kollege")
        await require_agent_access("a1", KOLLEGE, self.db, nutzen=True)
        with self.assertRaises(HTTPException) as e:
            await require_agent_access("a1", KOLLEGE, self.db)
        self.assertEqual(e.exception.status_code, 403)
        # Aeltere Freigabe und Besitzer: beides
        for wer in (ALT, BESITZER):
            await require_agent_access("a1", wer, self.db)
            await require_agent_access("a1", wer, self.db, nutzen=True)
        with self.assertRaises(HTTPException):
            await require_agent_access("a1", FREMD, self.db, nutzen=True)

    async def test_chat_endpunkte_offen_einstellungen_zu(self):
        await self._freigeben("kollege")
        r = await api.get_chat_sessions("a1", user=KOLLEGE, db=self.db)
        self.assertEqual(r["sessions"], [])
        verwalten = [
            lambda: api.get_agent_freigaben("a1", user=KOLLEGE, db=self.db),
            lambda: api._check_owner("a1", KOLLEGE, self.db),
        ]
        for aufruf in verwalten:
            with self.assertRaises(HTTPException) as e:
                await aufruf()
            self.assertEqual(e.exception.status_code, 403)

    async def test_sichtbarkeit_fuer_verwaltung_ohne_nur_nutzen(self):
        """Gedaechtnis, Zeitplaene, Auswertungen … fragen eigene_agent_ids — dort
        taucht ein nur zum Nutzen freigegebener Agent nicht auf."""
        await self._freigeben("kollege")
        self.assertNotIn("a1", await ownership.eigene_agent_ids(KOLLEGE, self.db))
        self.assertEqual(await ownership.nur_nutzbare_agent_ids(KOLLEGE, self.db), {"a1"})
        self.assertIn("a1", await ownership.eigene_agent_ids(ALT, self.db))
        self.assertEqual(await ownership.nur_nutzbare_agent_ids(BESITZER, self.db), set())
        ids = set((await self.db.execute(ownership.verwaltende_freigaben("kollege"))).scalars().all())
        self.assertEqual(ids, set())

    async def test_agentendaten_fuer_nur_nutzer_gekuerzt(self):
        daten = {"id": "a1", "name": "Assistenz", "state": "running", "webhook_token": "geheim",
                 "config": {"avatar": {"farbe": "blau"}, "telegram_key": "x"}, "budget_usd": 50}
        aus = api.fuer_nur_nutzer(daten)
        self.assertNotIn("webhook_token", aus)
        self.assertNotIn("budget_usd", aus)
        self.assertEqual(aus["config"], {"avatar": {"farbe": "blau"}})
        self.assertTrue(aus["nur_nutzen"])

    async def test_meldungen_nur_die_eigenen(self):
        from unittest.mock import AsyncMock, patch

        from app.api import notifications as meldungen

        await self._freigeben("kollege")
        self.db.add_all([
            Notification(agent_id="a1", title="t", message="Budget erreicht"),
            Notification(agent_id="a1", title="t", message="Deine Antwort", empfaenger_id="kollege"),
        ])
        await self.db.commit()
        with patch.object(meldungen, "_visible_agent_ids", AsyncMock(return_value=["a1"])):
            r = await meldungen.list_notifications(unread_only=False, limit=50, user=KOLLEGE, db=self.db)
            self.assertEqual({n["message"] for n in r["notifications"]}, {"Deine Antwort"})
            r = await meldungen.list_notifications(unread_only=False, limit=50, user=BESITZER, db=self.db)
            self.assertEqual({n["message"] for n in r["notifications"]}, {"Budget erreicht"})


if __name__ == "__main__":
    unittest.main()
