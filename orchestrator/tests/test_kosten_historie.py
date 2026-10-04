"""Gelöschte Chats und Aufgaben nehmen ihre Kosten nicht mit (#896).

Die Kostenquelle summiert ``chat_messages.cost_usd`` und ``tasks.cost_usd``. Wer
Zeilen löschte — Aufbewahrungsfrist, „Chat löschen“, „Alle Chats löschen“,
„Agent mit Daten löschen“, Aufgabe löschen, Müllabfuhr des Schedulers —, löschte
die Kosten mit: das Monatsbudget setzte sich zurück, „seit Beginn“ sank.

Jetzt werden die Kosten vor dem Löschen in ``kosten_historie`` verdichtet (Tag,
Agent, Besitzer, Quelle, Betrag — kein Inhalt), und ``kosten()`` zählt Historie
plus noch vorhandene Zeilen. Geprüft gegen eine echte (SQLite-)Datenbank: Vorher
und nachher dieselben Summen — weder verloren noch doppelt.
"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.core import kosten as k
from app.models.chat_message import ChatMessage
from app.models.chat_session import ChatSession
from app.models.kosten_historie import KostenHistorie
from app.models.task import Task
from app.models.task_rating import TaskRating
from tests.test_kosten_eine_quelle import JETZT, KostenBasis

ALT = JETZT - timedelta(days=40)


class Basis(KostenBasis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.models.agent_message import AgentMessage
        from app.models.agent_plan_item import AgentPlanItem
        from app.models.agent_todo import AgentTodo
        from app.models.audit_log import AuditLog
        from app.models.memory import AgentMemory, AgentMemoryLink, AgentMemoryTag
        from app.models.notification import Notification

        async with self.engine.begin() as conn:
            for model in (KostenHistorie, ChatSession, TaskRating, AuditLog, AgentMemory,
                          AgentMemoryLink, AgentMemoryTag, AgentTodo, AgentPlanItem,
                          Notification, AgentMessage):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        async with self.Session() as db:
            # Eine alte Unterhaltung von a1 in einem eigenen Gespräch.
            db.add(ChatMessage(agent_id="a1", session_id="alt", message_id="alt1",
                               role="assistant", content="geheim", cost_usd=5.0, timestamp=ALT))
            await db.commit()

    async def summen(self) -> dict:
        async with self.Session() as db:
            monat = k.monatsbeginn()
            return {
                "anlage_gesamt": round((await k.kosten(db, k.Bereich.anlage())).gesamt, 6),
                "anlage_monat": round((await k.kosten(db, k.Bereich.anlage(), seit=monat)).gesamt, 6),
                "a1_gesamt": round((await k.kosten(db, k.Bereich.agent("a1"))).gesamt, 6),
                "a1_monat": round((await k.kosten(db, k.Bereich.agent("a1"), seit=monat)).gesamt, 6),
                "ua_monat": round((await k.kosten(db, k.Bereich.besitzer("ua"), seit=monat)).gesamt, 6),
            }

    async def chatzeilen(self, **filter_) -> list[ChatMessage]:
        async with self.Session() as db:
            stmt = select(ChatMessage)
            for spalte, wert in filter_.items():
                stmt = stmt.where(getattr(ChatMessage, spalte) == wert)
            return list((await db.execute(stmt)).scalars().all())


class Aufbewahrungsfrist(Basis):
    async def test_alte_chats_weg_kosten_bleiben(self):
        from app.core import aufbewahrung

        vorher = await self.summen()

        class Einstellungen:
            werte = {aufbewahrung.SCHLUESSEL_CHAT: "30"}

            async def get(self, schluessel):
                return self.werte.get(schluessel)

            async def set(self, schluessel, wert):
                self.werte[schluessel] = wert

        async with self.Session() as db:
            erg = await aufbewahrung.aufraeumen(db, Einstellungen(), now=JETZT)
        self.assertEqual(erg["chat_nachrichten_geloescht"], 1)
        self.assertEqual(await self.chatzeilen(session_id="alt"), [])
        self.assertEqual(await self.summen(), vorher)
        # Die Historie kennt Betrag, Tag, Agent und Besitzer — keinen Inhalt.
        async with self.Session() as db:
            zeile = (await db.execute(select(KostenHistorie))).scalar_one()
        self.assertEqual((zeile.agent_id, zeile.user_id, zeile.quelle), ("a1", "ua", k.QUELLE_CHAT))
        self.assertAlmostEqual(zeile.betrag_usd, 5.0)
        self.assertEqual(zeile.tag, ALT.date())


class ChatLoeschen(Basis):
    async def test_ein_gespraech_loeschen(self):
        from app.api import agents

        vorher = await self.summen()
        async with self.Session() as db:
            with patch.object(agents, "_check_owner", AsyncMock()):
                await agents.delete_chat_session("a1", "s", force=True, user=SimpleNamespace(id="ua"), db=db)
        self.assertEqual(await self.chatzeilen(agent_id="a1", session_id="s"), [])
        self.assertEqual(await self.summen(), vorher)

    async def test_alle_gespraeche_loeschen_angeheftete_bleiben(self):
        from app.api import agents

        async with self.Session() as db:
            db.add(ChatSession(agent_id="a1", session_id="alt", pinned=True))
            await db.commit()
        vorher = await self.summen()
        async with self.Session() as db:
            with patch.object(agents, "_check_owner", AsyncMock()):
                await agents.delete_all_chat_sessions("a1", user=SimpleNamespace(id="ua"), db=db)
        # Das angeheftete Gespräch bleibt — und wird nicht zusätzlich verdichtet.
        self.assertEqual(len(await self.chatzeilen(session_id="alt")), 1)
        self.assertEqual(await self.chatzeilen(agent_id="a1", session_id="s"), [])
        self.assertEqual(await self.summen(), vorher)

    async def test_verwerfen_verschiebt_die_kosten_mit(self):
        """„Ab hier verwerfen“ verschiebt Nachrichten in ein Sicherungsgespräch —
        die Kopie trägt die Kosten, sonst gingen sie mit dem Original verloren."""
        from app.core import chat_history

        async with self.Session() as db:
            db.add(ChatMessage(agent_id="a1", session_id="s", message_id="m-davor",
                               role="user", content="frage", timestamp=JETZT - timedelta(minutes=5)))
            await db.commit()
        vorher = await self.summen()
        async with self.Session() as db:
            erg = await chat_history.rewind(db, "a1", "s", "m-davor")
            await db.commit()
        self.assertTrue(erg["ok"])
        self.assertEqual(await self.summen(), vorher)


class AgentMitDatenLoeschen(Basis):
    async def test_kosten_bleiben_beim_besitzer(self):
        from app.core.agent_manager import AgentManager

        vorher = await self.summen()
        async with self.Session() as db:
            manager = AgentManager.__new__(AgentManager)
            manager.db = db
            await manager._loesche_agentendaten("a1")
            await db.commit()
        self.assertEqual(await self.chatzeilen(agent_id="a1"), [])
        nachher = await self.summen()
        self.assertEqual(nachher["anlage_gesamt"], vorher["anlage_gesamt"])
        self.assertEqual(nachher["anlage_monat"], vorher["anlage_monat"])
        # Das Nutzerbudget des Besitzers sinkt nicht.
        self.assertEqual(nachher["ua_monat"], vorher["ua_monat"])


class AufgabeLoeschen(Basis):
    async def test_aufgabe_loeschen_kosten_bleiben(self):
        from app.core.task_router import TaskRouter

        vorher = await self.summen()
        async with self.Session() as db:
            router = TaskRouter.__new__(TaskRouter)
            router.db = db
            self.assertTrue(await router.delete_task("t1"))
        async with self.Session() as db:
            self.assertIsNone(await db.get(Task, "t1"))
        self.assertEqual(await self.summen(), vorher)


class KeineDoppelzaehlung(Basis):
    async def test_je_agent_und_je_tag_stimmen_nach_dem_verdichten(self):
        async with self.Session() as db:
            vorher = await k.kosten(db, k.Bereich.anlage(), je_tag=True)
            await k.verdichten(db, k.QUELLE_CHAT, ChatMessage.agent_id == "a1")
            # Verdichten allein (ohne Löschen) wäre doppelt — darum gehört es in
            # dieselbe Transaktion wie das Löschen. Hier: löschen, dann zählen.
            from sqlalchemy import delete

            await db.execute(delete(ChatMessage).where(ChatMessage.agent_id == "a1"))
            await db.commit()
            nachher = await k.kosten(db, k.Bereich.anlage(), je_tag=True)
        self.assertAlmostEqual(nachher.gesamt, vorher.gesamt)
        self.assertEqual(set(nachher.je_tag), set(vorher.je_tag))
        for tag, betrag in vorher.je_tag.items():
            self.assertAlmostEqual(nachher.je_tag[tag], betrag)
        self.assertAlmostEqual(nachher.je_agent["a1"], vorher.je_agent["a1"])


if __name__ == "__main__":
    unittest.main()
