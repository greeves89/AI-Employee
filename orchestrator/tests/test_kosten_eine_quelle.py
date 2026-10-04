"""Eine Kostenquelle (#896): Aufgaben UND Chat, nutzergetrennt, je Zeitraum.

Bis v1.362 gab es drei Kostenbegriffe nebeneinander: das Dashboard zählte nur
Aufgaben (im Browser), die Budget-Kachel alle Aufgaben aller Zeiten ohne Chat
(auch gelöschter Agenten), die Liste darunter Aufgaben + Chat des Monats, und
die Budgetprüfung nur Aufgaben des Monats. Im Markttest standen deshalb 89,26 €
über einer Liste, die zusammen 8,50 € ergab.

Geprüft wird gegen eine echte (SQLite-)Datenbank, nicht gegen den Quelltext.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import kosten as k
from app.models.agent import Agent, AgentState
from app.models.chat_message import ChatMessage
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole

JETZT = datetime.now(timezone.utc)
LETZTER_MONAT = k.monatsbeginn() - timedelta(days=3)


class KostenBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001 — schon registriert
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from app.models.kosten_historie import KostenHistorie  # #896: gelöschte Kosten
        async with self.engine.begin() as conn:
            for model in (User, Agent, Task, ChatMessage, KostenHistorie):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add_all([
                User(id="ua", email="a@example.invalid", name="A", password_hash="x",
                     role=UserRole.MEMBER),
                User(id="ub", email="b@example.invalid", name="B", password_hash="x",
                     role=UserRole.MEMBER),
                Agent(id="a1", name="A1", state=AgentState.IDLE, user_id="ua", config={}),
                Agent(id="a2", name="A2", state=AgentState.IDLE, user_id="ua", config={}),
                Agent(id="b1", name="B1", state=AgentState.IDLE, user_id="ub", config={}),
                # Diesen Monat
                self._task("t1", "a1", 1.00, JETZT),
                self._task("t2", "a2", 0.50, JETZT),
                self._task("t3", "b1", 4.00, JETZT),
                self._chat(1, "a1", 0.25, JETZT),
                self._chat(2, "b1", 2.00, JETZT),
                # Letzten Monat — zählt nur für „seit Beginn"
                self._task("t4", "a1", 10.00, LETZTER_MONAT),
                self._chat(3, "a1", 3.00, LETZTER_MONAT),
                # Gelöschter Agent: Aufgaben verlieren ihren Agenten (agent_id=None),
                # Chatzeilen behalten die alte Kennung.
                self._task("t5", None, 7.00, JETZT),
                self._chat(4, "weg", 0.75, JETZT),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _task(tid, agent_id, kosten, wann):
        return Task(id=tid, title=tid, prompt="p", status=TaskStatus.COMPLETED,
                    agent_id=agent_id, cost_usd=kosten, created_at=wann)

    @staticmethod
    def _chat(nr, agent_id, kosten, wann, rolle="assistant"):
        return ChatMessage(agent_id=agent_id, session_id="s", message_id=f"m{nr}",
                           role=rolle, content="x", cost_usd=kosten, timestamp=wann)


class EinAgent(KostenBasis):
    async def test_aufgaben_und_chat_im_monat(self):
        async with self.Session() as db:
            erg = await k.kosten(db, k.Bereich.agent("a1"), seit=k.monatsbeginn())
        self.assertAlmostEqual(erg.aufgaben, 1.00)
        self.assertAlmostEqual(erg.chat, 0.25)
        self.assertAlmostEqual(erg.gesamt, 1.25)

    async def test_seit_beginn_enthaelt_den_vormonat(self):
        async with self.Session() as db:
            erg = await k.kosten(db, k.Bereich.agent("a1"))
        self.assertAlmostEqual(erg.gesamt, 14.25)

    async def test_nur_eine_quelle(self):
        async with self.Session() as db:
            nur_chat = await k.kosten(db, k.Bereich.agent("a1"), seit=k.monatsbeginn(),
                                      quellen=(k.QUELLE_CHAT,))
        self.assertAlmostEqual(nur_chat.gesamt, 0.25)
        self.assertEqual(nur_chat.aufgaben, 0.0)


class GanzeAnlage(KostenBasis):
    async def test_geloeschte_agenten_zaehlen_extra(self):
        async with self.Session() as db:
            erg = await k.kosten(db, k.Bereich.anlage(), seit=k.monatsbeginn())
        # 1 + 0.5 + 4 + 0.25 + 2 + 7 + 0.75
        self.assertAlmostEqual(erg.gesamt, 15.50)
        self.assertAlmostEqual(erg.geloescht, 7.75)
        self.assertNotIn(None, erg.je_agent)
        self.assertNotIn("weg", erg.je_agent)
        # Die Summe der Liste plus „gelöschte Agenten" ergibt die Kachel — immer.
        self.assertAlmostEqual(sum(erg.je_agent.values()) + erg.geloescht, erg.gesamt)
        self.assertAlmostEqual(erg.je_agent["a1"], 1.25)

    async def test_je_tag(self):
        async with self.Session() as db:
            erg = await k.kosten(db, k.Bereich.anlage(), seit=k.monatsbeginn(), je_tag=True)
        self.assertAlmostEqual(sum(erg.je_tag.values()), erg.gesamt)
        self.assertIn(JETZT.date().isoformat(), erg.je_tag)


class Nutzertrennung(KostenBasis):
    """Ein Mitglied sieht nur die Kosten seiner eigenen Agenten."""

    async def _bereich(self, user, sichtbar):
        async with self.Session() as db:
            with patch("app.core.ownership.visible_agent_ids", AsyncMock(return_value=sichtbar)):
                bereich = await k.bereich_fuer_nutzer(user, db)
            return bereich, await k.kosten(db, bereich, seit=k.monatsbeginn())

    async def test_mitglied_sieht_nur_eigene(self):
        _, erg = await self._bereich(SimpleNamespace(id="ua", role=UserRole.MEMBER), {"a1", "a2"})
        self.assertAlmostEqual(erg.gesamt, 1.75)
        self.assertEqual(set(erg.je_agent), {"a1", "a2"})
        self.assertEqual(erg.geloescht, 0.0)

    async def test_mitglied_ohne_agenten_sieht_nichts(self):
        """Eine leere Menge heisst „nichts" — nie „alles"."""
        _, erg = await self._bereich(SimpleNamespace(id="uc", role=UserRole.MEMBER), set())
        self.assertEqual(erg.gesamt, 0.0)

    async def test_admin_sieht_alles(self):
        _, erg = await self._bereich(SimpleNamespace(id="adm", role=UserRole.ADMIN), None)
        self.assertAlmostEqual(erg.gesamt, 15.50)

    async def test_besitzer_fuer_das_nutzerbudget(self):
        async with self.Session() as db:
            erg = await k.kosten(db, k.Bereich.besitzer("ub"), seit=k.monatsbeginn())
        self.assertAlmostEqual(erg.gesamt, 6.00)


class ChatAntworten(KostenBasis):
    async def test_zaehlt_nur_antworten_im_bereich(self):
        async with self.Session() as db:
            db.add(self._chat(9, "a1", None, JETZT, rolle="user"))
            await db.commit()
            n = await k.chat_antworten(db, k.Bereich.agenten({"a1", "a2"}), seit=k.monatsbeginn())
        self.assertEqual(n, 1)


class BudgetSeite(KostenBasis):
    """Admin-Übersicht: „Kosten diesen Monat" = Summe der Liste (+ gelöschte Agenten)."""

    async def test_monat_und_seit_beginn(self):
        from app.api import admin

        async with self.Session() as db:
            out = await admin.get_admin_overview(user=SimpleNamespace(id="adm"), db=db)
            liste = await k.kosten(db, k.Bereich.anlage(), seit=k.monatsbeginn())
        kosten = out["cost"]
        self.assertAlmostEqual(kosten["monat_usd"], 15.50)
        self.assertAlmostEqual(
            sum(liste.je_agent.values()) + kosten["geloescht_monat_usd"], kosten["monat_usd"])
        self.assertAlmostEqual(kosten["total_usd"], 28.50)


class Betragsanzeige(unittest.TestCase):
    def test_euro_mit_kurs(self):
        with patch.object(k.settings, "display_currency", "EUR"), \
                patch.object(k.settings, "usd_eur_rate", 0.5):
            self.assertEqual(k.betrag_anzeigen(10), "5,00 €")

    def test_usd(self):
        with patch.object(k.settings, "display_currency", "USD"):
            self.assertEqual(k.betrag_anzeigen(1234.5), "1.234,50 $")


if __name__ == "__main__":
    unittest.main()
