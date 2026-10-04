"""Das ``done`` eines Chat-Zuges landet zusaetzlich in einer Redis-Liste.

Pub/Sub allein ging bei einem Orchestrator-Neustart verloren (03.10.2026) — die
Schlussantwort fehlte dann dauerhaft im Chat. Die Liste holt der Orchestrator nach.
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.log_publisher import LogPublisher


class DoneDauerhaftTests(unittest.IsolatedAsyncioTestCase):
    def _publisher(self):
        # Ueber den echten Konstruktor: seit #900 haelt der Publisher einen
        # Textpuffer, den ``__new__`` allein nicht anlegt.
        redis = MagicMock()
        redis.publish = AsyncMock()
        redis.rpush = AsyncMock()
        redis.ltrim = AsyncMock()
        return LogPublisher(redis, "a1")

    async def test_done_kommt_in_die_liste_des_agenten(self):
        pub = self._publisher()
        await pub.publish_chat("m1", "done", {"text": "Fertig"})
        pub.redis.rpush.assert_awaited_once()
        schluessel, nachricht = pub.redis.rpush.await_args.args
        self.assertEqual(schluessel, "agent:a1:chat:done")
        self.assertEqual(json.loads(nachricht)["data"]["text"], "Fertig")
        pub.redis.ltrim.assert_awaited_once_with("agent:a1:chat:done", -200, -1)

    async def test_zwischenstaende_kommen_nicht_hinein(self):
        pub = self._publisher()
        await pub.publish_chat("m1", "text", {"text": "Zwischenstand"})
        pub.redis.rpush.assert_not_awaited()

    async def test_fehler_der_liste_stoert_das_live_ereignis_nicht(self):
        pub = self._publisher()
        pub.redis.rpush = AsyncMock(side_effect=RuntimeError("weg"))
        await pub.publish_chat("m1", "done", {"text": "Fertig"})
        self.assertEqual(pub.redis.publish.await_count, 2)


if __name__ == "__main__":
    unittest.main()
