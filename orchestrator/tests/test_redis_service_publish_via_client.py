"""RedisService has no ``publish`` method of its own — only ``self.client``
(the underlying aioredis connection) does. Calling ``some_redis_service.publish(...)``
directly raises ``AttributeError`` at runtime, silently swallowing whatever the
call was trying to alert about.

Found live on 2026-09-17: a container restart triggers the "job crashed across
restart" alert path in ``app/main.py``'s lifespan, which called
``app.state.redis.publish(...)`` directly. Every such alert failed with
``AttributeError: 'RedisService' object has no attribute 'publish'`` — caught
only in the container logs (limited retention), never reaching the operator.

This is a source-scan guard rather than an execution test: the bug lives
inside a large inline FastAPI lifespan block that isn't otherwise unit-tested.
The regex is deliberately narrow (a literal ``.redis.publish(``) so it does not
false-positive on ``agent_bot.py``'s legitimate ``redis.publish(...)`` call,
where ``redis`` is a raw ``aioredis.from_url(...)`` client, not a RedisService
wrapper — that call has no leading dot before "redis".
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_BAD_CALL = re.compile(r"\.redis\.publish\(")


class RedisServicePublishGoesThroughClientTests(unittest.TestCase):
    def test_no_bare_redis_service_publish_calls_in_orchestrator(self):
        treffer = []
        for path in (ROOT / "orchestrator" / "app").rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in _BAD_CALL.finditer(text):
                zeile = text.count("\n", 0, match.start()) + 1
                treffer.append(f"{path.relative_to(ROOT)}:{zeile}")
        self.assertEqual(
            treffer, [],
            "RedisService hat kein eigenes publish() -- nur self.client.publish(): "
            f"{treffer}",
        )

    def test_the_fixed_callsite_uses_client(self):
        main_py = (ROOT / "orchestrator" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("app.state.redis.client.publish(", main_py)


if __name__ == "__main__":
    unittest.main()
