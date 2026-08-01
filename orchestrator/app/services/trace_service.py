"""Decision-Trace assembly for the Task time-travel view (issue #387).

Turns the raw per-step execution log (``TaskStep``) into a grouped, human-readable
timeline — thought -> tool call (input) -> matching tool result (output) -> next
step — where each entry carries a per-step duration derived from timestamps. It
also folds each ``tool_result`` into the ``tool_call`` that produced it (matched via
``tool_use_id``), attaches the task's governance audit events, and a cost summary.

Read-only: this only surfaces data that is already logged (``TaskStep`` written by
``_persist_task_steps``, ``AuditLog`` written across the API). No side effects.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.task import Task
from app.models.task_step import TaskStep


def _duration_ms(a: datetime | None, b: datetime | None) -> int | None:
    if not a or not b:
        return None
    return max(0, int((b - a).total_seconds() * 1000))


def _status(task: Task) -> str:
    s = task.status
    return s.value if hasattr(s, "value") else str(s)


async def assemble_trace(task_id: str, db: AsyncSession) -> dict | None:
    """Assemble the enriched decision-trace for one task, or None if it doesn't exist.

    Ownership/authorization is the caller's responsibility (mirrors get_task_steps).
    """
    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not task:
        return None

    steps = (await db.execute(
        select(TaskStep).where(TaskStep.task_id == task_id).order_by(TaskStep.sequence.asc())
    )).scalars().all()

    # Index tool_result rows by tool_use_id so each tool_call can attach its output,
    # and remember which ones get folded so we can hide the standalone result entry.
    results_by_id: dict[str, TaskStep] = {}
    for s in steps:
        if s.event_type == "tool_result":
            tid = (s.event_data or {}).get("tool_use_id")
            if tid:
                results_by_id[tid] = s

    consumed: set[str] = set()
    entries: list[dict] = []
    n = len(steps)

    for i, s in enumerate(steps):
        data = s.event_data or {}
        # duration = gap to the next step (or task completion for the last step)
        nxt_ts = steps[i + 1].timestamp if i + 1 < n else task.completed_at
        entry: dict = {
            "sequence": s.sequence,
            "type": s.event_type,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
            "duration_ms": _duration_ms(s.timestamp, nxt_ts),
        }

        if s.event_type == "text":
            entry["text"] = data.get("text") or data.get("content") or ""
        elif s.event_type == "tool_call":
            entry["tool"] = data.get("tool")
            entry["input"] = data.get("input")
            tid = data.get("tool_use_id")
            res = results_by_id.get(tid) if tid else None
            if res is not None:
                consumed.add(tid)
                rdata = res.event_data or {}
                entry["result"] = rdata.get("content")
                entry["result_timestamp"] = res.timestamp.isoformat() if res.timestamp else None
                entry["tool_duration_ms"] = _duration_ms(s.timestamp, res.timestamp)
        elif s.event_type == "tool_result":
            entry["content"] = data.get("content")  # kept only if orphaned (see filter below)
            entry["_tool_use_id"] = data.get("tool_use_id")
        elif s.event_type == "error":
            entry["error"] = data.get("error") or data.get("content") or data
        elif s.event_type == "result":
            entry["summary"] = data
        else:
            entry["data"] = data

        entries.append(entry)

    # Drop the standalone tool_result entries that were folded into their tool_call.
    entries = [
        e for e in entries
        if not (e["type"] == "tool_result" and e.get("_tool_use_id") in consumed)
    ]
    for e in entries:
        e.pop("_tool_use_id", None)

    audits = (await db.execute(
        select(AuditLog).where(AuditLog.task_id == task_id).order_by(AuditLog.created_at.asc())
    )).scalars().all()
    governance = [
        {
            "event_type": a.event_type,
            "command": a.command,
            "outcome": a.outcome,
            "exit_code": a.exit_code,
            "timestamp": a.created_at.isoformat() if a.created_at else None,
        }
        for a in audits
    ]

    summary = {
        "title": task.title,
        "status": _status(task),
        "model": task.model,
        "cost_usd": task.cost_usd,
        "input_tokens": task.input_tokens,
        "output_tokens": task.output_tokens,
        "duration_ms": task.duration_ms,
        "num_turns": task.num_turns,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }

    return {
        "task_id": task_id,
        "agent_id": task.agent_id,
        "summary": summary,
        "governance": governance,
        "total_steps": len(steps),
        "entries": entries,
    }
