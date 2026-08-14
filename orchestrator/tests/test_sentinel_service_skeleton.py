"""Tests for the SentinelService skeleton (issue #590, part of Sentinel epic #588).

Pins the two things a future #591/#592 implementation must not break:
  - `_handle_event` dispatches `_stop_agent` + `_notify` in parallel, only when
    `_scan` returns a triggered verdict.
  - the skeleton's own `_scan` never triggers (no detection rules yet), and
    malformed pubsub payloads are skipped instead of crashing the loop.
No live Redis needed — a minimal fake stands in for RedisService.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock

from app.services.sentinel_service import SentinelService, SentinelVerdict


def _service() -> SentinelService:
    fake_redis = AsyncMock()
    fake_redis.client = AsyncMock()
    return SentinelService(fake_redis)


class TestSentinelServiceSkeleton(unittest.IsolatedAsyncioTestCase):
    async def test_scan_never_triggers_by_default(self):
        service = _service()
        verdict = await service._scan("agent-1", {"agent_id": "agent-1", "type": "created"})
        self.assertIsNone(verdict)

    async def test_handle_event_skips_when_scan_does_not_trigger(self):
        service = _service()
        service._stop_agent = AsyncMock()
        service._notify = AsyncMock()

        await service._handle_event({"agent_id": "agent-1", "type": "created"})

        service._stop_agent.assert_not_awaited()
        service._notify.assert_not_awaited()

    async def test_handle_event_dispatches_stop_and_notify_in_parallel_on_trigger(self):
        service = _service()
        service._scan = AsyncMock(
            return_value=SentinelVerdict(triggered=True, reason="test_rule", excerpt="leaked secret")
        )
        service._stop_agent = AsyncMock()
        service._notify = AsyncMock()

        await service._handle_event({"agent_id": "agent-1", "type": "tool_call"})

        service._stop_agent.assert_awaited_once_with("agent-1", "test_rule")
        service._notify.assert_awaited_once_with("agent-1", "test_rule", "leaked secret")

    async def test_handle_event_ignores_events_without_agent_id(self):
        service = _service()
        service._scan = AsyncMock()

        await service._handle_event({"type": "created"})

        service._scan.assert_not_called()

    async def test_handle_message_skips_malformed_json(self):
        service = _service()
        service._handle_event = AsyncMock()

        await service._handle_message(b"not-json{{{")

        service._handle_event.assert_not_awaited()

    async def test_handle_message_decodes_bytes_and_routes_dict_payload(self):
        service = _service()
        service._handle_event = AsyncMock()

        await service._handle_message(b'{"agent_id": "agent-1", "type": "created"}')

        service._handle_event.assert_awaited_once_with({"agent_id": "agent-1", "type": "created"})

    async def test_handle_message_ignores_non_dict_payload(self):
        service = _service()
        service._handle_event = AsyncMock()

        await service._handle_message(b"[1, 2, 3]")

        service._handle_event.assert_not_awaited()

    async def test_stop_sets_running_false(self):
        service = _service()
        service._running = True
        service.stop()
        self.assertFalse(service._running)


if __name__ == "__main__":
    unittest.main()
