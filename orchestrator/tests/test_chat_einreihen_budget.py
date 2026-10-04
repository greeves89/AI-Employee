"""Chat-Nachrichten laufen durch dieselbe Budgetprüfung wie Aufgaben (#898).

Vorher schrieben Web-Chat, Kiosk, Telegram, die Kanäle (Teams, Slack, WhatsApp,
Discord), die Sprachfront, /goal und die Rückmeldungen delegierter Aufträge
ungeprüft in ``agent:{id}:chat``. Jetzt geht jede dieser Stellen über
``chat_auftrag.einreihen``. Geprüft wird das Verhalten (wird eingereiht? was
sieht der Mensch?) gegen eine echte SQLite-Datenbank — und zusätzlich, dass es
keine zweite Tür an der Prüfung vorbei gibt.
"""

import json
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import budget, chat_auftrag
from app.models.agent import Agent, AgentState
from app.models.chat_message import ChatMessage
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.user import User, UserRole

APP = Path(__file__).resolve().parents[1] / "app"


class FakeRedis:
    def __init__(self):
        self.listen: dict[str, list] = {}
        self.veroeffentlicht: list[tuple[str, dict]] = []

    async def lpush(self, key, wert):
        self.listen.setdefault(key, []).insert(0, wert)

    async def publish(self, kanal, wert):
        self.veroeffentlicht.append((kanal, json.loads(wert)))

    async def set(self, *a, **kw):
        return True

    async def setex(self, *a, **kw):
        return True


class EinreihenBasis(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles

        try:
            compiles(JSONB, "sqlite")(lambda *a, **kw: "JSON")
        except Exception:  # noqa: BLE001
            pass
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        from app.models.kosten_historie import KostenHistorie  # #896: gelöschte Kosten
        async with self.engine.begin() as conn:
            for model in (User, Agent, Task, ChatMessage, KostenHistorie, Notification):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = FakeRedis()
        self.einstellungen = patch.multiple(
            budget.settings, platform_budget_usd=0.0, budget_gilt_fuer_chat=True,
            model_provider="anthropic", display_currency="EUR", usd_eur_rate=0.5, create=True,
        )
        self.einstellungen.start()
        # Alle Wege oeffnen ihre Sitzung ueber die Fabrik der App — hier die Testdatenbank.
        self.fabrik = patch("app.db.session.async_session_factory", self.Session)
        self.fabrik.start()

    async def asyncTearDown(self):
        self.fabrik.stop()
        self.einstellungen.stop()
        await self.engine.dispose()

    async def agent(self, budget_usd=1.0, aktion="stop", mode="claude_code", kosten=2.0):
        async with self.Session() as db:
            db.add(User(id="u1", email="u1@example.invalid", name="U", role=UserRole.MEMBER))
            db.add(Agent(id="a1", name="Agent", state=AgentState.IDLE, user_id="u1",
                         config={}, mode=mode, budget_usd=budget_usd,
                         budget_exceeded_action=aktion, container_id="c1"))
            if kosten:
                db.add(Task(id="t1", title="t", prompt="p", status=TaskStatus.COMPLETED,
                            agent_id="a1", cost_usd=kosten, created_at=datetime.now(timezone.utc)))
            await db.commit()

    def eingereiht(self) -> list[dict]:
        return [json.loads(x) for x in self.redis.listen.get("agent:a1:chat", [])]

    async def einreihen(self, **payload):
        payload = {"id": "m1", "text": "Hallo", "model": None, **payload}
        return await chat_auftrag.einreihen(self.redis, "a1", payload)


class Entscheidung(EinreihenBasis):
    async def test_unter_budget_wird_eingereiht(self):
        await self.agent(budget_usd=10)
        erg = await self.einreihen()
        self.assertTrue(erg.eingereiht)
        self.assertEqual(self.eingereiht()[0]["text"], "Hallo")

    async def test_ueber_budget_wird_nicht_eingereiht(self):
        await self.agent()
        erg = await self.einreihen()
        self.assertFalse(erg.eingereiht)
        self.assertEqual(self.eingereiht(), [])
        # Deutscher Hinweis, in der Anzeigewaehrung
        self.assertIn("aufgebraucht", erg.hinweis)
        self.assertIn("€", erg.hinweis)

    async def test_offener_chat_sieht_den_hinweis(self):
        await self.agent()
        erg = await self.einreihen()
        kanal, ereignis = self.redis.veroeffentlicht[-1]
        self.assertEqual(kanal, "agent:a1:chat:response")
        self.assertEqual(ereignis["message_id"], "m1")
        self.assertEqual(ereignis["type"], "error")
        self.assertEqual(ereignis["data"]["message"], erg.hinweis)

    async def test_admin_wird_benachrichtigt(self):
        await self.agent()
        await self.einreihen()
        await self.einreihen(id="m2")
        async with self.Session() as db:
            an_admins = (await db.execute(
                select(Notification).where(Notification.agent_id == "system"))).scalars().all()
        self.assertEqual(len(an_admins), 1, "eine Meldung je Agent und Monat, nicht je Nachricht")

    async def test_sparmodus_setzt_das_modell_der_laufzeit(self):
        await self.agent(aktion="haiku")
        erg = await self.einreihen(model="claude-opus-5")
        self.assertTrue(erg.eingereiht)
        self.assertIn("haiku", self.eingereiht()[0]["model"])

    async def test_codex_im_sparmodus_wird_angehalten(self):
        await self.agent(aktion="haiku", mode="codex_cli")
        erg = await self.einreihen()
        self.assertFalse(erg.eingereiht)
        self.assertIn("kein günstigeres Modell", erg.hinweis)

    async def test_schalter_aus(self):
        await self.agent()
        with patch.object(budget.settings, "budget_gilt_fuer_chat", False):
            erg = await self.einreihen()
        self.assertTrue(erg.eingereiht)

    async def test_text_payload_geht_auch(self):
        await self.agent(budget_usd=10)
        erg = await chat_auftrag.einreihen(self.redis, "a1", json.dumps({"id": "m9", "text": "x"}))
        self.assertTrue(erg.eingereiht)
        self.assertEqual(self.eingereiht()[0]["id"], "m9")


class Zugaenge(EinreihenBasis):
    """Die Wege, die selbst antworten, sagen dem Menschen Bescheid."""

    async def test_kanal_bekommt_den_hinweis(self):
        from app.core import channel_gateway as gw

        await self.agent()
        nachricht = gw.InboundMessage(agent_id="a1", text="Hallo", channel=gw.CHANNEL_TEAMS,
                                      conversation_id="c", message_id="42", context={"x": 1})

        class R:
            client = self.redis

        with patch.object(gw, "persist_message", AsyncMock()), \
                patch.object(gw, "send_reply", AsyncMock(return_value=True)) as antwort:
            zugestellt = await gw.deliver(R(), nachricht, capture=False)
        self.assertFalse(zugestellt)
        self.assertEqual(self.eingereiht(), [])
        self.assertIn("aufgebraucht", antwort.await_args.args[3])

    async def test_sprachfront_bekommt_den_hinweis(self):
        from app.services.agent_chat_bridge import ask_agent_via_chat

        await self.agent()

        class Abo:
            async def get_message(self, **kw):  # pragma: no cover — darf nicht noetig sein
                raise AssertionError("bei gesperrtem Budget nicht auf eine Antwort warten")

            async def unsubscribe(self, *a):
                pass

            async def aclose(self):
                pass

        class R:
            client = self.redis

            async def subscribe(self, kanal):
                return Abo()

        antwort = await ask_agent_via_chat(R(), "a1", "Hallo", timeout=1)
        self.assertIn("aufgebraucht", antwort)
        self.assertEqual(self.eingereiht(), [])

    async def test_kiosk_lehnt_ab(self):
        from app.api import kiosk
        from app.api.kiosk import KioskChatSend

        await self.agent()

        class R:
            client = self.redis

        async with self.Session() as db:
            antwort = await kiosk.kiosk_chat_send("a1", KioskChatSend(text="Hallo"), db=db, redis=R())
            zeilen = (await db.execute(select(ChatMessage).where(
                ChatMessage.session_id == antwort["session_id"]))).scalars().all()
        self.assertFalse(antwort["eingereiht"])
        self.assertIn("aufgebraucht", antwort["hinweis"])
        self.assertEqual(self.eingereiht(), [])
        # Der Kiosk liest den Verlauf: dort steht der Hinweis als Fehlerzeile.
        fehler = [z for z in zeilen if z.role == "error"]
        self.assertEqual(len(fehler), 1)
        self.assertIn("aufgebraucht", fehler[0].content)


class KeineZweiteTuer(unittest.TestCase):
    """Wer in ``agent:{id}:chat`` schreibt, tut es über ``chat_auftrag.einreihen``.

    Ergänzt die Verhaltenstests oben: Eine neue Stelle, die direkt einreiht,
    wäre wieder ein Weg am Budget vorbei — genau der Fehler aus #898.
    """

    MUSTER = re.compile(r"""(?:lpush|rpush)\(\s*f?["']agent:\{[^}]*\}:chat["']""")

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
