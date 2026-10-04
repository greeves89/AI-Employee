"""Aufgaben und Agenten-Nachrichten laufen durch dieselbe Budgetprüfung wie der Chat (#898).

Die Sicherheitsprüfung fand Wege, die direkt in ``agent:{id}:tasks`` bzw.
``agent:{id}:messages`` schrieben — Webhooks, ``send_task`` über MCP, Nachrichten
zwischen Agenten, Besprechungen, Rückmeldungen fertiger Teilaufgaben. Jede
Nachricht in ``:messages`` startet beim Empfänger einen Modelllauf; mit
aufgebrauchtem Budget arbeitete der Agent dort einfach weiter.

Jetzt gehen diese Wege über ``chat_auftrag.aufgabe_einreihen`` bzw.
``chat_auftrag.nachricht_einreihen``. Geprüft wird das Verhalten gegen eine
echte SQLite-Datenbank und zusätzlich, dass es keine weitere Tür gibt.
"""

import json
import re
import unittest
from datetime import datetime, timezone

from sqlalchemy import select

from app.core import budget, chat_auftrag
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from tests.test_chat_einreihen_budget import APP, EinreihenBasis, FakeRedis


class FakeRedisMitRechts(FakeRedis):
    async def rpush(self, key, wert):
        self.listen.setdefault(key, []).append(wert)

    async def llen(self, key):
        return len(self.listen.get(key, []))


def redis_dienst(fake):
    """Der echte ``RedisService`` — mit der Attrappe als Verbindung."""
    from app.services.redis_service import RedisService

    dienst = RedisService("redis://example.invalid")
    dienst.client = fake
    return dienst


class Basis(EinreihenBasis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.redis = FakeRedisMitRechts()

    def liste(self, art: str) -> list[dict]:
        return [json.loads(x) for x in self.redis.listen.get(f"agent:a1:{art}", [])]


class Aufgaben(Basis):
    async def test_push_task_ist_die_tuer(self):
        """``RedisService.push_task`` — der Weg aller Aufrufer — prüft selbst und
        vermerkt die gesperrte Aufgabe als gescheitert."""
        await self.agent()
        async with self.Session() as db:
            db.add(Task(id="t9", title="t", prompt="p", status=TaskStatus.QUEUED, agent_id="a1"))
            await db.commit()
        erg = await redis_dienst(self.redis).push_task("a1", json.dumps({"id": "t9", "prompt": "p"}))
        self.assertFalse(erg.eingereiht)
        self.assertEqual(self.liste("tasks"), [])
        async with self.Session() as db:
            aufgabe = await db.get(Task, "t9")
        self.assertEqual(aufgabe.status, TaskStatus.FAILED)
        self.assertIn("aufgebraucht", aufgabe.error)

    async def test_unter_budget_wird_eingereiht(self):
        await self.agent(budget_usd=10)
        erg = await chat_auftrag.aufgabe_einreihen(self.redis, "a1", {"id": "t9", "prompt": "p"})
        self.assertTrue(erg.eingereiht)
        self.assertEqual(self.liste("tasks")[0]["id"], "t9")

    async def test_ueber_budget_nicht_eingereiht_und_gemeldet(self):
        await self.agent()
        erg = await chat_auftrag.aufgabe_einreihen(self.redis, "a1", {"id": "t9", "prompt": "p"})
        self.assertFalse(erg.eingereiht)
        self.assertIn("aufgebraucht", erg.hinweis)
        self.assertEqual(self.liste("tasks"), [])
        async with self.Session() as db:
            an_admins = (await db.execute(
                select(Notification).where(Notification.agent_id == "system"))).scalars().all()
        self.assertEqual(len(an_admins), 1)

    async def test_chatschalter_gilt_nicht_fuer_aufgaben(self):
        """„Budget gilt für Chat" aus — Aufgaben bleiben trotzdem gesperrt."""
        from unittest.mock import patch

        await self.agent()
        with patch.object(budget.settings, "budget_gilt_fuer_chat", False):
            erg = await chat_auftrag.aufgabe_einreihen(self.redis, "a1", {"id": "t9", "prompt": "p"})
        self.assertFalse(erg.eingereiht)

    async def test_sparmodus_setzt_das_modell(self):
        await self.agent(aktion="haiku")
        erg = await chat_auftrag.aufgabe_einreihen(
            self.redis, "a1", {"id": "t9", "prompt": "p", "model": "claude-opus-5"})
        self.assertTrue(erg.eingereiht)
        self.assertIn("haiku", self.liste("tasks")[0]["model"])
        self.assertEqual(erg.modell, self.liste("tasks")[0]["model"])


class Nachrichten(Basis):
    async def test_ueber_budget_nicht_zugestellt(self):
        await self.agent()
        erg = await chat_auftrag.nachricht_einreihen(self.redis, "a1", {"id": "n1", "text": "x"})
        self.assertFalse(erg.eingereiht)
        self.assertEqual(self.liste("messages"), [])

    async def test_vorne_einreihen_bleibt_moeglich(self):
        """Besprechungen legen ihre Beiträge ans andere Ende (``rpush``)."""
        await self.agent(budget_usd=10)
        await chat_auftrag.nachricht_einreihen(self.redis, "a1", {"id": "n1"})
        await chat_auftrag.nachricht_einreihen(self.redis, "a1", {"id": "n2"}, vorne=True)
        self.assertEqual([n["id"] for n in self.liste("messages")], ["n1", "n2"])

    async def test_unbekannter_empfaenger_geht_durch(self):
        """Der Moderator einer Besprechung ist kein Agent mit Budget."""
        erg = await chat_auftrag.nachricht_einreihen(self.redis, "moderator-x", {"id": "n1"})
        self.assertTrue(erg.eingereiht)


class Zugaenge(Basis):
    async def test_send_task_ueber_mcp(self):
        from app.api import mcp_agent
        from app.models.agent import Agent

        await self.agent()
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            out = await mcp_agent._call_tool(
                "send_task", {"prompt": "Bericht schreiben"}, agent, db,
                redis_dienst(self.redis))
            aufgaben = (await db.execute(select(Task).where(Task.id != "t1"))).scalars().all()
        self.assertTrue(out.get("isError"))
        self.assertIn("aufgebraucht", out["content"][0]["text"])
        self.assertEqual(self.liste("tasks"), [])
        # Die angelegte Aufgabe steht ehrlich als gescheitert da, nicht als „wartet".
        self.assertEqual(len(aufgaben), 1)
        self.assertEqual(aufgaben[0].status, TaskStatus.FAILED)
        self.assertIn("aufgebraucht", aufgaben[0].error)

    async def test_webhook_auftrag(self):
        from app.api import webhooks

        await self.agent()
        task = Task(id="w1", title="Webhook", prompt="p", status=TaskStatus.QUEUED, agent_id="a1",
                    created_at=datetime.now(timezone.utc))
        eintrag = await webhooks._auftrag_zustellen(
            redis_dienst(self.redis), "a1", task, {"id": "w1", "prompt": "p", "model": None})
        self.assertFalse(eintrag["eingereiht"])
        self.assertIn("aufgebraucht", eintrag["hinweis"])
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(self.liste("tasks"), [])


class KeineWeitereTuer(unittest.TestCase):
    """Wer in ``agent:{id}:tasks`` oder ``agent:{id}:messages`` schreibt, tut es über
    die Helfer in ``chat_auftrag`` — für Aufgaben über ``RedisService.push_task``,
    das selbst prüft. Ein direktes ``lpush``/``rpush`` wäre wieder ein Weg am
    Budget vorbei."""

    MUSTER = re.compile(
        r"""(?:lpush|rpush)\(\s*f?["']agent:\{[^}]*\}:(?:tasks|messages)["']""")

    def test_niemand_reiht_direkt_ein(self):
        fundstellen = []
        for datei in APP.rglob("*.py"):
            if datei.name == "chat_auftrag.py":
                continue
            if self.MUSTER.search(datei.read_text(encoding="utf-8")):
                fundstellen.append(str(datei.relative_to(APP)))
        self.assertEqual(fundstellen, [])


if __name__ == "__main__":
    unittest.main()
