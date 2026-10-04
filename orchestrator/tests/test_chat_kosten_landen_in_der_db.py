"""Was ein Chat-Lauf gekostet hat, landet in der Datenbank — auf jedem Weg (#896/#898).

Live belegt: ``chat_messages.cost_usd`` stand bei Claude-Antworten auf 0, bei Codex
auf NULL. Neben dem Agenten (falsches Feld, fehlende Berechnung — siehe
``agent/tests/test_chat_kosten_alle_laufzeiten.py``) lag es auch hier:

* Der serverseitige Lauscher (``main._persist_chat_completion``) — der EINZIGE
  Schreiber, wenn kein Browser offen ist — legte den Betrag nur in ``meta`` ab,
  nie in die Spalte ``cost_usd``, die Dashboard und Budget summieren.
* Läufe ohne eigene Verlaufszeile (Telegram, Sprachfront) wurden übersprungen —
  samt ihren Kosten. Die stehen jetzt in ``kosten_historie`` (Tag, Agent,
  Besitzer, Betrag — kein Inhalt), genau einmal, auch bei doppelter Zustellung.

Gegen eine echte (SQLite-)Datenbank; geprüft wird, was ``core.kosten`` danach zählt.
"""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.core import kosten as k
from app.models.chat_message import ChatMessage
from app.models.kosten_historie import KostenHistorie
from tests.test_kosten_eine_quelle import KostenBasis


class _RedisClient:
    def __init__(self):
        self.gesetzt: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.gesetzt:
            return None
        self.gesetzt[key] = value
        return True

    async def publish(self, *_a, **_k):
        return 0


class _Redis:
    def __init__(self):
        self.client = _RedisClient()


def _done(mid, betrag, **mehr):
    daten = {"status": "completed", "text": "Erledigt.", "cost_usd": betrag,
             "duration_ms": 900, "num_turns": 1, "input_tokens": 1200,
             "output_tokens": 340}
    daten.update(mehr)
    return {"agent_id": "a1", "message_id": mid, "type": "done", "data": daten}


class LauscherSchreibtKosten(KostenBasis):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.models.notification import Notification

        async with self.engine.begin() as conn:
            await conn.run_sync(Notification.metadata.create_all,
                                tables=[Notification.__table__])
        self.redis = _Redis()
        for p in (
            patch("app.db.session.async_session_factory", self.Session),
            patch("app.services.chat_persistence.async_session_factory", self.Session),
            patch("app.core.ziel.nach_zug", AsyncMock()),
            patch("app.core.ankuendigung.nach_zug", lambda *a, **kw: None),
            patch("app.core.push.push_to_user", AsyncMock()),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def _nutzerzeile(self, mid, session="s-neu"):
        async with self.Session() as db:
            db.add(ChatMessage(agent_id="a1", session_id=session, message_id=mid,
                               role="user", content="Frage"))
            await db.commit()

    async def _chatkosten_a1(self) -> float:
        async with self.Session() as db:
            return (await k.kosten(db, k.Bereich.agent("a1"),
                                   quellen=[k.QUELLE_CHAT])).chat

    async def test_ein_chat_lauf_schreibt_einen_betrag_in_die_spalte(self):
        from app import main

        vorher = await self._chatkosten_a1()
        await self._nutzerzeile("mk1")
        await main._persist_chat_completion(self.redis, _done("mk1", 0.0123))

        async with self.Session() as db:
            zeile = await db.scalar(select(ChatMessage).where(
                ChatMessage.message_id == "mk1", ChatMessage.role == "assistant"))
        self.assertIsNotNone(zeile)
        self.assertAlmostEqual(zeile.cost_usd, 0.0123)
        self.assertGreater(zeile.cost_usd, 0)
        self.assertEqual(zeile.input_tokens, 1200)
        self.assertEqual(zeile.output_tokens, 340)
        self.assertAlmostEqual(await self._chatkosten_a1() - vorher, 0.0123)

    async def test_doppelte_zustellung_zaehlt_einmal(self):
        """Pub/Sub UND die dauerhafte Liste liefern dasselbe ``done``."""
        from app import main

        vorher = await self._chatkosten_a1()
        await self._nutzerzeile("mk2")
        await main._persist_chat_completion(self.redis, _done("mk2", 0.05))
        await main._persist_chat_completion(self.redis, _done("mk2", 0.05))
        self.assertAlmostEqual(await self._chatkosten_a1() - vorher, 0.05)

    async def test_lauf_ohne_verlaufszeile_wird_trotzdem_abgerechnet(self):
        """Telegram, Sprachfront: kein Gesprächseintrag — die Kosten zählen
        trotzdem, für den Agenten und für das Budget seines Besitzers."""
        from app import main

        async with self.Session() as db:
            vorher_besitzer = (await k.kosten(db, k.Bereich.besitzer("ua"))).gesamt
        vorher = await self._chatkosten_a1()

        await main._persist_chat_completion(self.redis, _done("tg-77", 0.20))
        await main._persist_chat_completion(self.redis, _done("tg-77", 0.20))  # doppelt

        self.assertAlmostEqual(await self._chatkosten_a1() - vorher, 0.20)
        async with self.Session() as db:
            nachher_besitzer = (await k.kosten(db, k.Bereich.besitzer("ua"))).gesamt
            historie = (await db.execute(select(KostenHistorie))).scalars().all()
            antworten = (await db.execute(select(ChatMessage).where(
                ChatMessage.message_id == "tg-77"))).scalars().all()
        self.assertAlmostEqual(nachher_besitzer - vorher_besitzer, 0.20)
        self.assertEqual(len(historie), 1)
        self.assertEqual(historie[0].quelle, k.QUELLE_CHAT)
        self.assertEqual(historie[0].user_id, "ua")
        self.assertEqual(antworten, [], "kein Gesprächseintrag erfunden")

    async def test_kostenloser_lauf_ohne_verlaufszeile_bucht_nichts(self):
        from app import main

        await main._persist_chat_completion(self.redis, _done("tg-78", 0))
        async with self.Session() as db:
            self.assertEqual((await db.execute(select(KostenHistorie))).scalars().all(), [])


class EineKennzahlenquelle(unittest.TestCase):
    """Browser-Verbindung und Lauscher lesen die Kennzahlen eines ``done`` an EINER Stelle."""

    def test_kennzahlen(self):
        from app.services.chat_persistence import done_kennzahlen

        k_ = done_kennzahlen({"cost_usd": 0.1, "duration_ms": 5, "num_turns": 2,
                              "input_tokens": 7, "output_tokens": 3,
                              "cached_tokens": 4, "reasoning_tokens": 0})
        self.assertEqual(k_["cost_usd"], 0.1)
        self.assertEqual(k_["input_tokens"], 7)
        self.assertEqual(k_["cached_tokens"], 4)
        self.assertNotIn("reasoning_tokens", k_, "Nullwerte der Feinaufschlüsselung entfallen")


if __name__ == "__main__":
    unittest.main()
