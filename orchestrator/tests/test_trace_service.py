"""Decision-Trace assembly (issue #387) — unit tests for the grouping logic.

Covers ``trace_service.assemble_trace``:
  1. tool_result is folded into its tool_call (matched via tool_use_id) and the
     standalone result entry is dropped.
  2. an orphan tool_result (no matching call) is kept.
  3. per-step duration is the gap to the next step (last step -> task.completed_at).
  4. governance audit events and the cost summary are attached.
  5. missing task -> None.

The DB is a stand-in: ``db.execute`` returns purpose-built results in call order
(task -> steps -> audits). No real database is touched.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.services.trace_service import assemble_trace

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _step(seq, etype, data, offset_s):
    s = MagicMock()
    s.sequence = seq
    s.event_type = etype
    s.event_data = data
    s.timestamp = T0 + timedelta(seconds=offset_s)
    return s


def _task(**kw):
    t = MagicMock()
    defaults = dict(
        id="task-1", title="Test", status="completed", model="claude",
        cost_usd=0.12, input_tokens=100, output_tokens=50, duration_ms=5000,
        num_turns=3, agent_id="agent-1",
        started_at=T0, completed_at=T0 + timedelta(seconds=5),
    )
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(t, k, v)
    return t


def _audit(event_type, command, outcome, offset_s):
    a = MagicMock()
    a.event_type = event_type
    a.command = command
    a.outcome = outcome
    a.exit_code = 0
    a.created_at = T0 + timedelta(seconds=offset_s)
    return a


def _db(task, steps, audits):
    r_task = MagicMock(); r_task.scalar_one_or_none.return_value = task
    r_steps = MagicMock(); r_steps.scalars.return_value.all.return_value = steps
    r_aud = MagicMock(); r_aud.scalars.return_value.all.return_value = audits
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[r_task, r_steps, r_aud])
    return db


class AssembleTraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_task_returns_none(self):
        db = MagicMock()
        r = MagicMock(); r.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=r)
        self.assertIsNone(await assemble_trace("nope", db))

    async def test_tool_result_folded_into_call(self):
        steps = [
            _step(0, "text", {"text": "denke nach"}, 0),
            _step(1, "tool_call", {"tool": "bash", "input": {"cmd": "ls"}, "tool_use_id": "t1"}, 1),
            _step(2, "tool_result", {"tool_use_id": "t1", "content": "file.txt"}, 2),
            _step(3, "text", {"text": "fertig"}, 3),
        ]
        tr = await assemble_trace("task-1", _db(_task(), steps, []))
        # standalone tool_result dropped -> 3 entries
        self.assertEqual(len(tr["entries"]), 3)
        call = next(e for e in tr["entries"] if e["type"] == "tool_call")
        self.assertEqual(call["tool"], "bash")
        self.assertEqual(call["result"], "file.txt")           # folded in
        self.assertEqual(call["tool_duration_ms"], 1000)       # t1 call->result = 1s
        self.assertNotIn("tool_result", [e["type"] for e in tr["entries"]])

    async def test_orphan_tool_result_kept(self):
        steps = [
            _step(0, "tool_result", {"tool_use_id": "zzz", "content": "orphan"}, 0),
        ]
        tr = await assemble_trace("task-1", _db(_task(), steps, []))
        self.assertEqual(len(tr["entries"]), 1)
        self.assertEqual(tr["entries"][0]["type"], "tool_result")

    async def test_per_step_duration_and_last_uses_completed_at(self):
        steps = [
            _step(0, "text", {"text": "a"}, 0),   # -> next at +2 = 2000ms
            _step(1, "text", {"text": "b"}, 2),   # last -> completed_at (+5) = 3000ms
        ]
        tr = await assemble_trace("task-1", _db(_task(completed_at=T0 + timedelta(seconds=5)), steps, []))
        self.assertEqual(tr["entries"][0]["duration_ms"], 2000)
        self.assertEqual(tr["entries"][1]["duration_ms"], 3000)

    async def test_governance_and_summary_attached(self):
        steps = [_step(0, "text", {"text": "a"}, 0)]
        audits = [_audit("command_executed", "ls -la", "success", 1),
                  _audit("dlp_blocked", "send_mail", "blocked", 2)]
        tr = await assemble_trace("task-1", _db(_task(cost_usd=0.99, num_turns=7), steps, audits))
        self.assertEqual(len(tr["governance"]), 2)
        self.assertEqual(tr["governance"][1]["outcome"], "blocked")
        self.assertEqual(tr["summary"]["cost_usd"], 0.99)
        self.assertEqual(tr["summary"]["num_turns"], 7)
        self.assertEqual(tr["total_steps"], 1)


if __name__ == "__main__":
    unittest.main()
