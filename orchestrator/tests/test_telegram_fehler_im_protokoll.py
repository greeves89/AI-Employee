"""Was der Telegram-Nutzer als "Fehler: …" sieht, steht auch im Protokoll.

Am 2026-11-01 meldete der Bot nach jeder Antwort einen Fehler; im Protokoll der
Anlage stand davon nichts, und die Ursache liess sich nur nachstellen. Hier laeuft
der echte Antwort-Lauscher mit einem Fehler-Ereignis des Agenten.
"""

import asyncio
import json
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.telegram import agent_bot


class _PubSub:
    def __init__(self, nachrichten):
        self._nachrichten = list(nachrichten)

    async def subscribe(self, *_a):
        return None

    async def get_message(self, **_k):
        if self._nachrichten:
            return {"type": "message", "data": json.dumps(self._nachrichten.pop(0))}
        await asyncio.sleep(0.01)
        return None

    async def unsubscribe(self):
        return None

    async def aclose(self):
        return None


class FehlerImProtokoll(unittest.IsolatedAsyncioTestCase):
    async def test_fehler_geht_an_telegram_und_ins_protokoll(self):
        bot = agent_bot.TelegramAgentBot("a1", "Agent", "123:abc", "k")
        bot.app = MagicMock()
        bot.app.bot.send_message = AsyncMock()
        ereignis = {"message_id": "tg-7", "type": "error",
                    "data": {"message": "Failed to authenticate: OAuth token revoked"}}
        redis = MagicMock()
        redis.pubsub.return_value = _PubSub([ereignis])
        redis.aclose = AsyncMock()
        with patch.object(agent_bot.aioredis, "from_url", return_value=redis), \
                self.assertLogs(agent_bot.logger, logging.WARNING) as protokoll:
            lauscher = asyncio.create_task(bot._listen_responses(4711, "a1"))
            for _ in range(200):
                if bot.app.bot.send_message.await_count:
                    break
                await asyncio.sleep(0.01)
            lauscher.cancel()
            await asyncio.gather(lauscher, return_exceptions=True)
        bot.app.bot.send_message.assert_awaited_once_with(
            chat_id=4711, text="Fehler: Failed to authenticate: OAuth token revoked")
        zeile = "\n".join(protokoll.output)
        self.assertIn("Fehler an Chat 4711", zeile)
        self.assertIn("tg-7", zeile)
        self.assertIn("OAuth token revoked", zeile)


if __name__ == "__main__":
    unittest.main()
