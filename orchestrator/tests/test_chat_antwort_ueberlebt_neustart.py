"""Die Schlussantwort eines Agenten darf bei einem Orchestrator-Neustart nicht verloren gehen.

03.10.2026: Das ``done`` mit der fertigen Antwort lief nur ueber Redis Pub/Sub. Der
Orchestrator startete gerade neu, niemand hoerte zu — im Chat blieb ein Teilstueck
(``partial``), die Antwort fehlte fuer immer. Jetzt legt der Agent jedes ``done``
zusaetzlich in ``agent:{id}:chat:done`` ab, der Orchestrator holt es nach.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app import main


class _Redis:
    def __init__(self, listen):
        self.listen = {k: list(v) for k, v in listen.items()}

    async def scan_iter(self, match=None, count=None):
        for k in list(self.listen):
            yield k.encode()

    async def lpop(self, key):
        eintraege = self.listen.get(key) or []
        return eintraege.pop(0).encode() if eintraege else None


class NachtragTests(unittest.IsolatedAsyncioTestCase):
    async def _ein_lauf(self, listen):
        redis = type("R", (), {})()
        redis.client = _Redis(listen)
        gesichert = AsyncMock()

        class _Ende(BaseException):
            pass

        with patch.object(main, "_persist_chat_completion", gesichert), \
             patch.object(main.asyncio, "sleep", AsyncMock(side_effect=_Ende())):
            with self.assertRaises(_Ende):
                await main._drain_chat_completions(redis)
        return gesichert, redis.client

    async def test_liegengebliebene_antworten_werden_gesichert(self):
        done = {"agent_id": "a1", "message_id": "m1", "type": "done", "data": {"text": "Fertig"}}
        gesichert, client = await self._ein_lauf({"agent:a1:chat:done": [json.dumps(done)]})
        gesichert.assert_awaited_once()
        self.assertEqual(gesichert.await_args.args[1]["data"]["text"], "Fertig")
        self.assertEqual(client.listen["agent:a1:chat:done"], [], "die Liste wird abgeraeumt")

    async def test_kennung_kommt_aus_dem_schluessel_nicht_aus_der_nutzlast(self):
        gefaelscht = {"agent_id": "fremder", "message_id": "m1", "data": {"text": "x"}}
        gesichert, _ = await self._ein_lauf({"agent:a1:chat:done": [json.dumps(gefaelscht)]})
        self.assertEqual(gesichert.await_args.args[1]["agent_id"], "a1")

    async def test_kaputter_eintrag_blockiert_den_rest_nicht(self):
        gut = {"message_id": "m2", "data": {"text": "ok"}}
        gesichert, _ = await self._ein_lauf({"agent:a1:chat:done": ["{kein json", json.dumps(gut)]})
        gesichert.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
