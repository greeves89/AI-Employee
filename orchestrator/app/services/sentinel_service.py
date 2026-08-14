"""Sentinel core service skeleton (issue #590, part of Sentinel epic #588).

Event-driven counterpart to watchdog.py's poll-driven checks: instead of
scanning the DB on a timer like SchedulerService.run() does, SentinelService
subscribes to the orchestrator's own `agents:logs:all` Redis channel — the
same channel StreamManager.stream_all_logs() already streams to the admin UI,
populated by AgentManager._publish_event() (orchestrator/app/core/agent_manager.py)
as it executes/passes through each agent lifecycle event.

That channel choice is load-bearing, not incidental: per #588's manipulation-proof
analysis, a telemetry source the Sentinel acts on must never be something an
agent's own process can write, only something the orchestrator generates at the
point it actually performs an action. `agents:logs:all` already satisfies that
today; nothing here reads any per-agent-writable stream.

This is the Grundgerüst only (Teil 2/4, #590 scope point 1). The three hook
points below exist so the wiring is in place end to end, but carry no
production logic yet:
  - `_scan`       always returns None (no detection rules — that's #592,
                   the DLP #525/#564/#575 scan logic).
  - `_stop_agent` logs only (the privileged credential schema + the real
                   AgentManager.stop_agent() call land in #591).
  - `_notify`     logs only (wiring to notify_user/send_telegram and the
                   audit-log table land alongside #591/#592).
Because `sentinel_enabled` defaults to False and `_scan` never triggers even
when on, none of this has any observable effect until those follow-ups land.
"""

import asyncio
import json
import logging

from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

_AGENTS_LOGS_ALL_CHANNEL = "agents:logs:all"
# Pubsub reconnect backoff after an unexpected error (Redis restart, network
# blip). Short enough that a real incident isn't missed for long, long enough
# not to hot-loop against a Redis that is still down.
_RECONNECT_DELAY_SECONDS = 2


class SentinelVerdict:
    """Result of scanning one agent event.

    `triggered=False` is the only outcome this skeleton ever produces — real
    rules (secret/PII leakage, destructive commands, policy violations, ...)
    are #592's job. The shape exists now so #592 can fill in `_scan` without
    touching the dispatch path in `_handle_event`.
    """

    def __init__(self, triggered: bool, reason: str | None = None, excerpt: str | None = None):
        self.triggered = triggered
        self.reason = reason
        self.excerpt = excerpt


class SentinelService:
    """Central, privileged supervisor over all agents (Sentinel epic #588).

    Runs exactly once, inside the orchestrator process — never inside an
    agent container, unlike a per-agent self-check, which an agent under an
    injection attack could simply skip. See SchedulerService for the sibling
    "one background service per orchestrator process" pattern; the difference
    here is event-driven consumption instead of a 30s poll loop, because a
    harmful action needs to be caught as it happens, not up to 30s later.
    """

    def __init__(self, redis: RedisService):
        self.redis = redis
        self._running = False

    async def run(self) -> None:
        """Main loop: (re)subscribe to agents:logs:all and react to each event.

        Never lets a pubsub error kill the loop — same resilience contract as
        StreamManager.stream_all_logs(): log, back off, reconnect. A Sentinel
        that silently stops watching is worse than one that logs a warning and
        keeps trying; watchdog.py is expected to grow a liveness check on this
        service itself (#590 scope point 6) so a stuck Sentinel is its own alert
        rather than a silent gap.
        """
        logger.info("[Sentinel] Service started")
        self._running = True
        while self._running:
            try:
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "[Sentinel] pubsub consume error, reconnecting in %ss: %s",
                    _RECONNECT_DELAY_SECONDS, e,
                )
                await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    def stop(self) -> None:
        """Signal run() to exit after the current message (graceful shutdown)."""
        self._running = False

    async def _consume(self) -> None:
        """Hold one pubsub subscription open and hand each message to _handle_message."""
        if not self.redis.client:
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)
            return
        pubsub = await self.redis.subscribe(_AGENTS_LOGS_ALL_CHANNEL)
        try:
            while self._running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    await self._handle_message(message["data"])
                else:
                    await asyncio.sleep(0.01)
        finally:
            await pubsub.unsubscribe(_AGENTS_LOGS_ALL_CHANNEL)
            await pubsub.aclose()

    async def _handle_message(self, raw: bytes | str) -> None:
        """Decode one raw pubsub payload and route it, tolerating malformed events."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            event = json.loads(raw)
        except (ValueError, TypeError) as e:
            logger.warning("[Sentinel] Could not decode event, skipping: %s", e)
            return
        if not isinstance(event, dict):
            return
        await self._handle_event(event)

    async def _handle_event(self, event: dict) -> None:
        """Scan one decoded event; on a trigger, stop the agent and notify in parallel.

        `asyncio.gather` (not sequential awaits) per #590 scope point 4: a
        blocking stop_agent() call must never delay the human-facing alert.
        """
        agent_id = event.get("agent_id")
        if not agent_id:
            return
        verdict = await self._scan(agent_id, event)
        if verdict is None or not verdict.triggered:
            return
        reason = verdict.reason or "sentinel_triggered"
        await asyncio.gather(
            self._stop_agent(agent_id, reason),
            self._notify(agent_id, reason, verdict.excerpt),
            return_exceptions=True,
        )

    async def _scan(self, agent_id: str, event: dict) -> SentinelVerdict | None:
        """Hook point for detection logic. Skeleton: no rules yet, never triggers.

        Real implementation lands in #592 (DLP-style secret/PII/policy checks,
        building on #525/#564/#575) — this signature is the contract #592 fills in.
        """
        return None

    async def _stop_agent(self, agent_id: str, reason: str) -> None:
        """Hook point for the privileged stop path.

        Not wired to AgentManager.stop_agent() yet: that call needs the new
        Sentinel-exclusive credential schema from #591 (a third scheme, neither
        the human JWT nor the agent HMAC token — see #590's "Bereits vorhandene
        Bausteine"). Logs only until then.
        """
        logger.warning(
            "[Sentinel] stop_agent hook called for %s (reason=%s) — not yet wired, see #591",
            agent_id, reason,
        )

    async def _notify(self, agent_id: str, reason: str, excerpt: str | None) -> None:
        """Hook point for human escalation.

        Not wired to notify_user/send_telegram or the audit-log table yet
        (#591/#592). Logs only until then.
        """
        logger.warning(
            "[Sentinel] notify hook called for %s (reason=%s, excerpt=%r)",
            agent_id, reason, excerpt,
        )
