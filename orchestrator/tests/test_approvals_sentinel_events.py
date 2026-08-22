"""Sentinel epic #588, issue #591: approval-flow events reach the Sentinel pipeline.

`orchestrator/app/api/approvals.py` is one of two server-side decision points
(`command_policies.py` deliberately isn't, see the docstring on
`get_policies_for_agent`) where a specific command's fate is known to the
orchestrator, not just self-reported by the agent. This pins that:
  - `request_approval` publishes one "approval_requested" event carrying the
    command, its policy verdict (risk_level) and the reasoning.
  - `approve_request` / `deny_request` / `cancel_approval_request` each
    publish exactly one "approval_resolved" event with the outcome — once,
    at the actual resolution, not on every `/check/{id}` poll.
  - every published event is marked `source: "orchestrator"` so a future
    #592 scan rule can tell it apart from the agent-self-reported traffic
    that shares the same per-agent channel via `agent/app/log_publisher.py`.
  - since #590 the event goes to `agent:{id}:logs`, not to the globally
    writable `agents:logs:all` — the Sentinel derives attribution from the
    channel name, so an event outside the agent's namespace never arrives.
No live Redis needed — a minimal fake stands in for RedisService, same
pattern as test_approval_flood.py.
"""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api import approvals as api
from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.command_approval import ApprovalStatus, CommandApproval
from app.models.notification import Notification
from app.models.user import UserRole


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _admin():
    return SimpleNamespace(id="u1", role=UserRole.ADMIN, email="admin@example.test")


def _fake_redis():
    redis = AsyncMock()
    redis.client = AsyncMock()
    return redis


class ApprovalsSentinelEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, CommandApproval, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING,
                         user_id="u1", config={}))
            await db.commit()
        self.redis = _fake_redis()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _sentinel_calls(self):
        """Every publish() call aimed at the Sentinel pipeline channel, decoded."""
        out = []
        for call in self.redis.client.publish.await_args_list:
            channel, payload = call.args
            if channel == api._sentinel_pipeline_channel("a1"):
                out.append(json.loads(payload))
        return out

    async def _request(self, db, **kw):
        body = api.ApprovalRequest(
            tool=kw.pop("tool", "bash"),
            reasoning=kw.pop("reasoning", "rm -rf /tmp/x"),
            risk_level=kw.pop("risk_level", "high"),
            **kw,
        )
        with patch.object(api, "_get_redis", return_value=self.redis), \
             patch.object(api, "_push_ios_for_agent", new=AsyncMock()):
            return await api.request_approval(body, agent_auth={"agent_id": "a1"}, db=db)

    async def test_request_approval_publishes_one_approval_requested_event(self):
        async with self.Session() as db:
            await self._request(db)

        events = self._sentinel_calls()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["agent_id"], "a1")
        self.assertEqual(event["type"], "approval_requested")
        self.assertEqual(event["data"]["source"], "orchestrator")
        self.assertEqual(event["data"]["risk_level"], "high")
        self.assertEqual(event["data"]["tool"], "bash")

    async def test_repeated_identical_question_does_not_republish(self):
        # The dedupe path in request_approval returns early for a repeated
        # identical question — it must not double-publish either.
        async with self.Session() as db:
            await self._request(db, tool=None, question="Darf ich X?", reasoning="ctx")
            await self._request(db, tool=None, question="Darf ich X?", reasoning="ctx")

        events = [e for e in self._sentinel_calls() if e["type"] == "approval_requested"]
        self.assertEqual(len(events), 1)

    async def test_approve_publishes_one_resolved_event(self):
        async with self.Session() as db:
            await self._request(db)
            approval_id = (await db.execute(
                __import__("sqlalchemy").select(CommandApproval)
            )).scalars().first().id

            with patch.object(api, "_get_redis", return_value=self.redis):
                await api.approve_request(str(approval_id), user=_admin(), db=db)

        resolved = [e for e in self._sentinel_calls() if e["type"] == "approval_resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["data"]["status"], "approved")
        self.assertEqual(resolved[0]["data"]["source"], "orchestrator")

    async def test_deny_publishes_one_resolved_event_without_free_text_reason(self):
        # PR #596 review: `decision.reason` is human-supplied free text that can
        # carry secrets/PII and was only `scrub_log()`-ed (control chars only,
        # not real redaction) before reaching the Sentinel pipeline. It must be
        # excluded from the Sentinel payload entirely, not merely scrubbed.
        async with self.Session() as db:
            await self._request(db)
            approval_id = (await db.execute(
                __import__("sqlalchemy").select(CommandApproval)
            )).scalars().first().id

            with patch.object(api, "_get_redis", return_value=self.redis):
                await api.deny_request(
                    str(approval_id),
                    decision=api.ApprovalDecision(decision="deny", reason="zu riskant, api_key=sk-abc123"),
                    user=_admin(), db=db,
                )

        resolved = [e for e in self._sentinel_calls() if e["type"] == "approval_resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["data"]["status"], "denied")
        self.assertNotIn("reason", resolved[0]["data"])

    async def test_request_approval_event_excludes_free_text_reasoning(self):
        # Same rationale as above, for the approval_requested side.
        async with self.Session() as db:
            await self._request(db, reasoning="rm -rf /tmp/x, token=ghp_abcdefghijklmnopqrstuvwxyz0123456789")

        events = [e for e in self._sentinel_calls() if e["type"] == "approval_requested"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("reasoning", events[0]["data"])

    async def test_cancel_publishes_one_resolved_event(self):
        async with self.Session() as db:
            await self._request(db)
            approval_id = (await db.execute(
                __import__("sqlalchemy").select(CommandApproval)
            )).scalars().first().id

            with patch.object(api, "_get_redis", return_value=self.redis):
                await api.cancel_approval_request(str(approval_id), user=_admin(), db=db)

        resolved = [e for e in self._sentinel_calls() if e["type"] == "approval_resolved"]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["data"]["status"], "cancelled")

    async def test_event_goes_to_the_agents_own_namespace_not_the_global_channel(self):
        # #590: `agents:logs:all` is writable by every agent, so the Sentinel
        # stopped reading it. An orchestrator event published there would be
        # both unattributable and silently dropped.
        async with self.Session() as db:
            await self._request(db)

        channels = [c.args[0] for c in self.redis.client.publish.await_args_list]
        self.assertIn("agent:a1:logs", channels)
        self.assertNotIn("agents:logs:all", channels)

    async def test_no_redis_client_does_not_raise(self):
        # Best-effort like _publish_notification: a dead/absent Redis must
        # never break the approval flow itself.
        async with self.Session() as db:
            with patch.object(api, "_get_redis", return_value=None), \
                 patch.object(api, "_push_ios_for_agent", new=AsyncMock()):
                result = await api.request_approval(
                    api.ApprovalRequest(tool="bash", reasoning="ls", risk_level="medium"),
                    agent_auth={"agent_id": "a1"}, db=db,
                )
        self.assertEqual(result["status"], "pending")


if __name__ == "__main__":
    unittest.main()
