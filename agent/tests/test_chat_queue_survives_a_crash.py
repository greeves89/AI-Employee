"""Eine Chat-Nachricht darf einen Container-Absturz nicht mit ins Grab nehmen (#645).

brpop NIMMT die Nachricht aus der Queue. Stirbt der Container danach und vor dem
Ende der Verarbeitung (OOM, Neustart-Schleife beim Aufwecken), war sie weg —
genau so verschwand am 2026-08-23 eine Telegram-Sprachnachricht an einen
schlafenden Agenten. Jetzt liegt jede Nachricht waehrend der Verarbeitung in
einer Inflight-Liste und wird beim naechsten Start zurueck in die Queue gelegt.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock

from app.chat_consumer import ChatConsumer


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list] = {}

    async def rpop(self, key):
        items = self.lists.get(key) or []
        return items.pop() if items else None

    async def lpop(self, key):
        items = self.lists.get(key) or []
        return items.pop(0) if items else None

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def lrem(self, key, count, value):
        items = self.lists.get(key) or []
        if value in items:
            items.remove(value)
            return 1
        return 0


def _consumer(redis) -> ChatConsumer:
    c = ChatConsumer.__new__(ChatConsumer)
    c.agent_id = "a1"
    c.queue_name = "agent:a1:chat"
    c.redis = redis
    return c


class InflightRequeueTests(unittest.IsolatedAsyncioTestCase):
    async def test_leftovers_go_back_to_the_queue_in_old_order(self):
        redis = _FakeRedis()
        # Inflight vom letzten Absturz: b wurde NACH a gezogen, a ist die aeltere.
        redis.lists["agent:a1:chat:inflight"] = [b"b", b"a"]
        redis.lists["agent:a1:chat"] = [b"neu"]
        c = _consumer(redis)
        await c._requeue_inflight()
        # brpop liest rechts: erst a (aelteste), dann b, dann erst die neue.
        self.assertEqual(redis.lists["agent:a1:chat"], [b"neu", b"b", b"a"])
        self.assertEqual(redis.lists["agent:a1:chat:inflight"], [])

    async def test_empty_inflight_changes_nothing(self):
        redis = _FakeRedis()
        redis.lists["agent:a1:chat"] = [b"x"]
        c = _consumer(redis)
        await c._requeue_inflight()
        self.assertEqual(redis.lists["agent:a1:chat"], [b"x"])

    async def test_a_redis_error_does_not_kill_the_startup(self):
        redis = _FakeRedis()
        redis.lpop = AsyncMock(side_effect=RuntimeError("redis weg"))
        c = _consumer(redis)
        await c._requeue_inflight()  # darf nicht werfen


class InflightBookkeepingTests(unittest.IsolatedAsyncioTestCase):
    """Waehrend der Verarbeitung liegt die Nachricht in der Inflight-Liste,
    danach nicht mehr — sonst wuerde jeder Neustart Erledigtes wiederholen."""

    async def test_serial_loop_clears_inflight_after_processing(self):
        redis = _FakeRedis()
        c = _consumer(redis)
        # Die Buchfuehrung des Serial-Pfads nachstellen (brpop-Ergebnis in Hand):
        msg_json = b'{"id":"m1","text":"hallo"}'
        await redis.lpush(c.inflight_key, msg_json)
        self.assertIn(msg_json, redis.lists[c.inflight_key])
        await redis.lrem(c.inflight_key, 1, msg_json)
        self.assertNotIn(msg_json, redis.lists.get(c.inflight_key) or [])

    def test_both_loops_are_wired(self):
        import inspect
        from app import chat_consumer
        serial = inspect.getsource(chat_consumer.ChatConsumer._run_serial)
        self.assertIn("lpush(self.inflight_key", serial)
        self.assertIn("lrem(self.inflight_key", serial)
        parallel = inspect.getsource(chat_consumer.ChatConsumer._run_parallel)
        self.assertIn("lpush(self.inflight_key", parallel)
        worker = inspect.getsource(chat_consumer.ChatConsumer._lane_worker)
        self.assertIn("lrem(self.inflight_key", worker)
        start = inspect.getsource(chat_consumer.ChatConsumer.start)
        self.assertIn("_requeue_inflight", start)


class DrainPendingLaneTests(unittest.IsolatedAsyncioTestCase):
    """Live-Fehler beim Kunden (2026-08-31, wiederkehrend alle ~18min):
    'tuple' object has no attribute 'get' in _drain_pending. _run_parallel legt
    (msg, msg_json)-Tupel in die Lane (Zeile 741), _lane_worker packt sie korrekt
    aus (Zeile 755), _drain_pending tat es nicht und behandelte das Tupel selbst
    wie ein dict."""

    def _consumer_with_lane(self, items: list[tuple[dict, bytes]]) -> ChatConsumer:
        c = ChatConsumer.__new__(ChatConsumer)
        c.agent_id = "a1"
        c._lanes = {}
        c._handlers = {}
        lane: asyncio.Queue = asyncio.Queue()
        for item in items:
            lane.put_nowait(item)
        c._lanes["sk1"] = lane
        c._reset_handler = AsyncMock()
        c._prepare_text = lambda text, telegram_ctx, source, handler: text
        return c

    async def test_a_queued_tuple_does_not_crash_draining(self):
        c = self._consumer_with_lane([
            ({"text": "hallo"}, b'{"text": "hallo"}'),
            ({"text": "nochmal"}, b'{"text": "nochmal"}'),
        ])
        texts = await c._drain_pending("sk1")
        self.assertEqual(texts, ["hallo", "nochmal"])

    async def test_a_slash_reset_in_the_lane_still_resets(self):
        c = self._consumer_with_lane([
            ({"text": "hallo"}, b'{"text": "hallo"}'),
            ({"text": "/reset"}, b'{"text": "/reset"}'),
            ({"text": "danach"}, b'{"text": "danach"}'),
        ])
        texts = await c._drain_pending("sk1")
        c._reset_handler.assert_awaited_once_with("sk1")
        # /reset clears everything queued BEFORE it, "danach" survives.
        self.assertEqual(texts, ["danach"])


if __name__ == "__main__":
    unittest.main()
