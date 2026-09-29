"""Doppelt-Pruefung je Telegram-Bot: gleiche message_id in zwei Bot-Chats ist KEINE Dublette.

Gemeldeter Fall (Pi-Anlage mit vier Agenten-Bots, 2026-09-29): Eine weitergeleitete
Nachricht an den Social Manager kam nie beim Agenten an — im Log stand
``inbound text … gateway=716c81b0 … message=173``, aber keine Zeile
``[Gateway] telegram -> Agent``. Der Agent antwortete auf die Folgenachricht:
„Ich sehe keine URL in deiner Nachricht".

Ursache: Telegram vergibt message_id je Bot-Chat ab 1. Der Schluessel der
Doppelt-Pruefung war ``gateway:seen:telegram:173`` — ohne Bot. GosuDevs Bot hatte
am Vorabend (23:48) seine eigene Nr. 173 gesehen; 24 h lang galt damit jede
Nr. 173 in JEDEM Bot-Chat als schon verarbeitet. Dasselbe um 08:16 mit Nr. 182.
In drei Tagen Log kamen 13 Nummern in mehreren Bot-Chats vor.
"""

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

from app.core import channel_gateway as gw

_BOT_SRC = Path(__file__).resolve().parents[1] / "app/telegram/agent_bot.py"
_REDIS_URL = os.environ.get("REDIS_URL", "")

_GOSUDEV, _SOCIAL = "cdf74fae", "716c81b0"
_NILS = "1459222478"  # privater Chat: chat_id = Nutzer-ID, fuer jeden Bot dieselbe


class _FakeClient:
    def __init__(self):
        self.keys = {}
        self.lists = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])


class _FakeRedis:
    def __init__(self, client=None):
        self.client = client or _FakeClient()


def _tg(gateway, message_id, chat=_NILS, agent=None):
    return gw.InboundMessage(
        agent_id=agent or gateway, text="Hallo", channel=gw.CHANNEL_TELEGRAM,
        conversation_id=chat, message_id=str(message_id), gateway_id=gateway,
    )


class DoppeltPruefungTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_number_in_another_bot_chat_is_not_a_duplicate(self):
        """Der gemeldete Fall."""
        redis = _FakeRedis()
        self.assertFalse(await gw.already_seen(redis, _tg(_GOSUDEV, 173)))
        self.assertFalse(await gw.already_seen(redis, _tg(_SOCIAL, 173)))

    async def test_real_duplicates_are_still_caught(self):
        """Derselbe Bot, derselbe Chat, dieselbe Nummer — das bleibt eine Dublette."""
        redis = _FakeRedis()
        self.assertFalse(await gw.already_seen(redis, _tg(_SOCIAL, 173)))
        self.assertTrue(await gw.already_seen(redis, _tg(_SOCIAL, 173)))

    async def test_same_bot_other_chat_is_not_a_duplicate(self):
        """Zwei Nutzer schreiben demselben Bot — jede Nutzer-Unterhaltung zaehlt eigen."""
        redis = _FakeRedis()
        self.assertFalse(await gw.already_seen(redis, _tg(_SOCIAL, 5, chat="111")))
        self.assertFalse(await gw.already_seen(redis, _tg(_SOCIAL, 5, chat="222")))

    async def test_key_without_gateway_is_unchanged(self):
        """Teams wird abgefragt: ein neuer Schluessel wuerde nach dem Update alle
        Nachrichten des letzten Tages noch einmal zustellen."""
        redis = _FakeRedis()
        msg = gw.InboundMessage(agent_id="a1", text="x", channel=gw.CHANNEL_TEAMS,
                                conversation_id="c1", message_id="m1")
        await gw.already_seen(redis, msg)
        self.assertEqual(list(redis.client.keys), ["gateway:seen:teams:m1"])

    async def test_both_messages_reach_their_agents(self):
        """Ende zu Ende ueber deliver: beide landen in der Warteschlange."""
        redis = _FakeRedis()
        enqueued = []

        async def _enqueue(_redis, message):
            enqueued.append(message.agent_id)

        async def _nichts(*_a, **_k):
            return None

        with mock.patch.object(gw, "persist_message", _nichts), \
             mock.patch.object(gw, "capture_if_worthwhile", _nichts), \
             mock.patch.object(gw, "enqueue", _enqueue), \
             mock.patch("app.services.user_lifecycle.mark_agent_interaction", _nichts):
            self.assertTrue(await gw.deliver(redis, _tg(_GOSUDEV, 173)))
            self.assertTrue(await gw.deliver(redis, _tg(_SOCIAL, 173)))
            self.assertFalse(await gw.deliver(redis, _tg(_SOCIAL, 173)))
        self.assertEqual(enqueued, [_GOSUDEV, _SOCIAL])


class TelegramSetztDenBotTests(unittest.TestCase):
    def test_telegram_passes_its_bot_as_gateway(self):
        src = _BOT_SRC.read_text()
        self.assertIn("gateway_id=self.agent_id", src)


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
    async def test_the_reported_case_against_redis(self):
        import redis.asyncio as aioredis
        client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        chat = "test" + os.urandom(4).hex()
        try:
            redis = _FakeRedis(client)
            self.assertFalse(await gw.already_seen(redis, _tg(_GOSUDEV, 173, chat=chat)))
            self.assertFalse(await gw.already_seen(redis, _tg(_SOCIAL, 173, chat=chat)))
            self.assertTrue(await gw.already_seen(redis, _tg(_SOCIAL, 173, chat=chat)))
            ttl = await client.ttl(f"gateway:seen:telegram:{_SOCIAL}:{chat}:173")
            self.assertGreater(ttl, 0)
        finally:
            keys = [k async for k in client.scan_iter(f"gateway:seen:telegram:*:{chat}:*")]
            if keys:
                await client.delete(*keys)
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
