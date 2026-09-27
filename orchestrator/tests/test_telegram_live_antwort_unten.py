"""Telegram: Die Live-Antwort muss unten im Chat stehen, nicht irgendwo darueber.

Gemeldeter Fall (Pi-Anlage, 2026-09-27, Social Manager): Der Agent schickt einen
Artikel in sechs Teilen per send-message, der Nutzer schreibt waehrenddessen
nach („bist du noch da?"). Die Antwort „Ja, bin da! … Soll ich …?" steht in der
Datenbank — in Telegram erschien unten aber nur „⏱ 7.7s | 🔄 1 turns". Der
Text war in die Live-Nachricht geschrieben worden, die schon VOR den sechs
Teilen angelegt war, also weit oben im Verlauf. Der Agent wartete auf eine
Antwort auf eine Frage, die der Nutzer nie gesehen hatte.

Geprueft wird:
  1. chat_tail merkt sich je Bot und Chat die juengste Nachrichten-ID.
  2. Die fertige Antwort wird nur dann in-place bearbeitet, wenn die
     Live-Nachricht noch unten steht; sonst kommt sie neu unten an.
  3. Auch Zwischenstaende (Arbeitszeile) wandern nach unten.
  4. Die Werkzeug-API (_tg_request) und eingehende Nachrichten melden sich.
  5. Gegen echtes Redis: ZADD … GT haelt das Maximum (ohne REDIS_URL uebersprungen).
"""

import asyncio
import os
import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

from app.telegram import chat_tail
from app.telegram.agent_bot import TelegramAgentBot

_BOT_SRC = pathlib.Path(__file__).resolve().parents[1] / "app/telegram/agent_bot.py"
_REDIS_URL = os.environ.get("REDIS_URL", "")


class _FakeRedis:
    """Nur was chat_tail braucht: zadd (mit gt) und zscore."""

    def __init__(self, store, fail=False):
        self._store = store
        self._fail = fail

    async def zadd(self, key, mapping, gt=False):
        if self._fail:
            raise ConnectionError("redis weg")
        zset = self._store.setdefault(key, {})
        for member, score in mapping.items():
            if not gt or member not in zset or score > zset[member]:
                zset[member] = score

    async def zscore(self, key, member):
        if self._fail:
            raise ConnectionError("redis weg")
        return self._store.get(key, {}).get(member)

    async def aclose(self):
        pass


class _Chat:
    """Ein Telegram-Chat: IDs werden fortlaufend vergeben, in beide Richtungen."""

    def __init__(self):
        self.next_id = 100
        self.sent = []      # (message_id, text)
        self.edited = []    # (message_id, text)
        self.deleted = []   # message_id

    def new_id(self):
        self.next_id += 1
        return self.next_id


class _TgBot:
    def __init__(self, chat: _Chat):
        self._chat = chat

    async def send_message(self, chat_id, text, parse_mode=None):
        mid = self._chat.new_id()
        self._chat.sent.append((mid, text))
        return SimpleNamespace(message_id=mid)

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None):
        self._chat.edited.append((message_id, text))

    async def delete_message(self, chat_id, message_id):
        self._chat.deleted.append(message_id)


class _MitFakeRedis(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = {}
        p = mock.patch.object(chat_tail, "_client", lambda: _FakeRedis(self.store))
        p.start()
        self.addCleanup(p.stop)


class ChatTailTests(_MitFakeRedis):
    def test_ids_from_bot_api_results(self):
        msg = {"message_id": 7, "chat": {"id": 42}}
        self.assertEqual(chat_tail.sent_message_ids(msg), [(42, 7)])
        self.assertEqual(chat_tail.sent_message_ids([msg, {"message_id": 8, "chat": {"id": 42}}]),
                         [(42, 7), (42, 8)])
        self.assertEqual(chat_tail.sent_message_ids(True), [])
        self.assertEqual(chat_tail.sent_message_ids({}), [])

    def test_bot_id_is_token_prefix(self):
        self.assertEqual(chat_tail.bot_id_from_token("123456:ABC-def"), "123456")
        self.assertEqual(chat_tail.bot_id_from_token(None), "")

    async def test_only_newer_ids_count(self):
        await chat_tail.note("b1", 42, 10)
        await chat_tail.note("b1", 42, 7)   # aeltere ID kommt spaeter an
        self.assertEqual(await chat_tail.newest("b1", 42), 10)
        self.assertTrue(await chat_tail.is_buried("b1", 42, 9))
        self.assertFalse(await chat_tail.is_buried("b1", 42, 10))

    async def test_bots_do_not_share_counts(self):
        """chat_id ist im Privatchat die Nutzer-ID — je Bot ein eigener Chat."""
        await chat_tail.note("b1", 42, 500)
        self.assertFalse(await chat_tail.is_buried("b2", 42, 3))

    async def test_redis_failure_never_raises_and_keeps_old_behaviour(self):
        with mock.patch.object(chat_tail, "_client", lambda: _FakeRedis({}, fail=True)):
            await chat_tail.note("b1", 42, 10)
            self.assertFalse(await chat_tail.is_buried("b1", 42, 1))


class LiveAntwortTests(_MitFakeRedis):
    async def asyncSetUp(self):
        self.chat = _Chat()
        self.bot = TelegramAgentBot("a1", "Social Manager", "777:tok", "key")
        self.bot.app = SimpleNamespace(bot=_TgBot(self.chat))

        async def _kein_dlp():
            return False

        async def _unveraendert(text):
            return text

        self.bot._dlp_active = _kein_dlp
        self.bot._dlp_text = _unveraendert
        verdict = lambda text, **_: SimpleNamespace(blocked=False, output=text)  # noqa: E731
        p = mock.patch("app.core.dlp.evaluate_egress", mock.AsyncMock(side_effect=verdict))
        p.start()
        self.addCleanup(p.stop)

    async def _live_nachricht(self, text):
        await self.bot._live_update(1, text)
        return self.bot._live[1]["id"]

    async def test_unchanged_when_live_message_is_still_at_the_bottom(self):
        """Der Normalfall bleibt, wie er war: eine Nachricht, am Ende bearbeitet."""
        live = await self._live_nachricht("Ich schaue")
        await self.bot._live_update(1, "Ich schaue nach. Fertig.", final=True)
        self.assertEqual([m for m, _ in self.chat.sent], [live])
        self.assertEqual(self.chat.deleted, [])
        self.assertEqual(self.chat.edited[-1][0], live)

    async def test_final_answer_moves_below_messages_that_arrived_meanwhile(self):
        """Der gemeldete Fall."""
        live = await self._live_nachricht("Ich ueberarbeite den Artikel")
        # Artikel in Teilen ueber die Werkzeug-API, dann die Nachfrage des Nutzers.
        for _ in range(6):
            await chat_tail.note(self.bot._bot_id, 1, self.chat.new_id())
        await chat_tail.note(self.bot._bot_id, 1, self.chat.new_id())  # „bist du noch da?"

        await self.bot._live_update(1, "Ja, bin da! Soll ich weitermachen?", final=True)

        self.assertIn(live, self.chat.deleted)
        neu_id, neu_text = self.chat.sent[-1]
        self.assertGreater(neu_id, live)
        self.assertIn("Ja, bin da!", neu_text)
        self.assertNotIn(live, [m for m, _ in self.chat.edited])

    async def test_working_line_follows_to_the_bottom(self):
        """Auch der Arbeitsstand darf nicht oben im Verlauf haengen bleiben."""
        live = await self._live_nachricht("Erzeuge das Bild")
        await chat_tail.note(self.bot._bot_id, 1, self.chat.new_id())
        self.bot._live[1]["last"] = -1000.0  # 5-s-Drossel fuer den Test aufheben
        await self.bot._live_update(1, "Erzeuge das Bild", status="Bash: python logo.py")
        self.assertIn(live, self.chat.deleted)
        self.assertGreater(self.bot._live[1]["id"], live)

    async def test_own_chunked_messages_are_noted(self):
        await self.bot._send_chunked(1, "Hallo")
        self.assertEqual(await chat_tail.newest(self.bot._bot_id, 1), self.chat.sent[-1][0])

    async def test_inbound_messages_are_noted(self):
        update = SimpleNamespace(effective_message=SimpleNamespace(chat_id=1, message_id=555))
        await self.bot._note_inbound(update, None)
        self.assertEqual(await chat_tail.newest(self.bot._bot_id, 1), 555)

    def test_inbound_note_runs_before_the_real_handlers(self):
        self.assertIn("self._note_inbound), group=-1)", _BOT_SRC.read_text())


class WerkzeugApiTests(_MitFakeRedis):
    async def test_tool_sends_are_noted_per_bot(self):
        from app.api import telegram_actions

        class _Resp:
            status_code = 200

            def json(self):
                return {"ok": True, "result": {"message_id": 77, "chat": {"id": 1}}}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, *a, **k):
                return _Resp()

        with mock.patch.object(telegram_actions.httpx, "AsyncClient", _Client):
            await telegram_actions._tg_request("777:tok", "sendMessage", {"chat_id": 1})
            await telegram_actions._tg_request("777:tok", "getMe")
        self.assertEqual(await chat_tail.newest("777", 1), 77)


def _redis_da() -> bool:
    if not _REDIS_URL:
        return False

    async def _ping():
        import redis.asyncio as aioredis
        r = aioredis.from_url(_REDIS_URL)
        try:
            return await r.ping()
        finally:
            await r.aclose()

    try:
        return bool(asyncio.run(_ping()))
    except Exception:
        return False


@unittest.skipUnless(_redis_da(), "Kein erreichbares Redis (REDIS_URL).")
class GegenEchtesRedis(unittest.IsolatedAsyncioTestCase):
    async def test_gt_keeps_the_maximum(self):
        import redis.asyncio as aioredis
        with mock.patch.object(chat_tail, "_client",
                               lambda: aioredis.from_url(_REDIS_URL, decode_responses=True)):
            bot = "test" + os.urandom(4).hex()
            await chat_tail.note(bot, 42, 10)
            await chat_tail.note(bot, 42, 3)
            await chat_tail.note(bot, 43, 1)
            self.assertEqual(await chat_tail.newest(bot, 42), 10)
            self.assertEqual(await chat_tail.newest(bot, 43), 1)
            self.assertTrue(await chat_tail.is_buried(bot, 42, 9))
            r = aioredis.from_url(_REDIS_URL)
            try:
                await r.delete(chat_tail._KEY.format(bot_id=bot))
            finally:
                await r.aclose()


if __name__ == "__main__":
    unittest.main()
