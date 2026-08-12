"""Workflow execution engine (#392).

A small, safe state machine that advances a ``WorkflowRun`` one move at a time,
driven by the scheduler tick. No ``eval`` — conditions are structured data, prompt
substitution is a literal ``{{step_id}}`` replace. Agent-task steps are async: the
engine creates a Task and returns; the next tick resumes once the task is terminal.

Pure helpers (``substitute``, ``eval_check``, ``next_after``) are fully unit-tested;
``advance_run`` wires them to the DB + TaskRouter.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.models.task import Task, TaskStatus
from app.models.workflow import Workflow, WorkflowRun

logger = logging.getLogger(__name__)

MAX_STEPS_PER_TICK = 50   # guard against a mis-wired infinite condition loop
MAX_TOTAL_STEPS = 500     # hard cap on a single run's length

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_\-]+)\s*\}\}")


def substitute(text: str, context: dict) -> str:
    """Replace ``{{step_id}}`` with that step's result from the run context."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        sid = m.group(1)
        entry = context.get(sid)
        if isinstance(entry, dict):
            return str(entry.get("result", ""))
        return str(entry) if entry is not None else ""

    return _PLACEHOLDER.sub(repl, text)


def eval_check(check: dict, context: dict) -> bool:
    """Evaluate a structured condition against the run context (no eval).

    check = {"step": "s1", "op": "contains|equals|not_equals|not_empty|is_empty", "value": "..."}
    """
    if not check:
        return False
    entry = context.get(check.get("step", ""))
    result = ""
    if isinstance(entry, dict):
        result = str(entry.get("result", ""))
    elif entry is not None:
        result = str(entry)
    op = check.get("op", "not_empty")
    value = check.get("value", "")
    if op == "contains":
        return str(value).lower() in result.lower()
    if op == "equals":
        return result.strip() == str(value).strip()
    if op == "not_equals":
        return result.strip() != str(value).strip()
    if op == "is_empty":
        return result.strip() == ""
    # default / "not_empty"
    return result.strip() != ""


def next_after(step: dict, context: dict) -> str | None:
    """The id of the next step to run after ``step`` (resolves conditions)."""
    if step.get("type") == "condition":
        return step.get("true") if eval_check(step.get("check", {}), context) else step.get("false")
    return step.get("next")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def start_run(
    workflow: Workflow, db, run_id: str | None = None, context: dict | None = None
) -> WorkflowRun:
    """Create a fresh run positioned at the workflow's start step.

    ``context`` setzt Startwerte, auf die Schritte per ``{{name}}`` zugreifen —
    genau die Mechanik, die auch Schritt-Ergebnisse benutzen. Ein von aussen
    ausgeloester Lauf (#392) legt dort seine Nutzlast unter ``trigger`` ab, ohne
    dass es dafuer eine zweite Ersetzungslogik braeuchte.
    """
    defn = workflow.definition or {}
    run = WorkflowRun(
        id=run_id or f"wfr_{uuid.uuid4().hex[:12]}",
        workflow_id=workflow.id,
        status="running",
        context=dict(context or {}),
        current_step=defn.get("start"),
        current_task_id=None,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def advance_run(run: WorkflowRun, workflow: Workflow, db, router) -> None:
    """Advance one run as far as possible without blocking.

    Returns after either finishing the run, or hitting an agent-task/wait that must
    resume on a later tick. Persists changes. Never raises (marks run failed instead).
    """
    try:
        defn = workflow.definition or {}
        steps: dict = defn.get("steps", {})

        # 1) Waiting on an agent task? Check whether it finished.
        if run.current_task_id:
            task = (await db.get(Task, run.current_task_id))
            if task is None or task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return  # still running — try again next tick
            if task.status != TaskStatus.COMPLETED:
                run.status = "failed"
                run.error = f"Schritt '{run.current_step}' fehlgeschlagen (Task {task.status.value if hasattr(task.status,'value') else task.status})."
                run.completed_at = _now()
                await db.commit()
                return
            ctx = dict(run.context or {})
            ctx[run.current_step] = {"result": task.result or "", "task_id": task.id}
            run.context = ctx
            run.steps_done = (run.steps_done or 0) + 1
            run.current_task_id = None
            cur = steps.get(run.current_step, {})
            run.current_step = next_after(cur, run.context)

        # 2) Waiting on a timer?
        if run.resume_at is not None:
            if run.resume_at > _now():
                return
            run.resume_at = None

        # 3) Walk synchronous steps until an async boundary or the end.
        guard = 0
        while run.current_step and guard < MAX_STEPS_PER_TICK:
            guard += 1
            if (run.steps_done or 0) >= MAX_TOTAL_STEPS:
                run.status = "failed"
                run.error = "Maximale Schrittzahl überschritten (mögliche Endlosschleife)."
                run.completed_at = _now()
                await db.commit()
                return
            step = steps.get(run.current_step)
            if not step:
                run.status = "failed"
                run.error = f"Unbekannter Schritt '{run.current_step}'."
                run.completed_at = _now()
                await db.commit()
                return
            stype = step.get("type")

            if stype == "agent_task":
                prompt = substitute(step.get("prompt", ""), run.context)
                task = await router.create_and_route_task(
                    title=step.get("title") or "Workflow-Schritt",
                    prompt=prompt,
                    agent_id=step.get("agent_id"),
                    metadata={"workflow_run": run.id, "workflow_step": run.current_step},
                )
                run.current_task_id = task.id
                await db.commit()
                return  # resume when the task completes

            if stype == "wait":
                run.resume_at = _now() + timedelta(seconds=int(step.get("seconds", 0)))
                run.steps_done = (run.steps_done or 0) + 1
                run.current_step = step.get("next")
                await db.commit()
                return  # resume after the delay

            if stype == "condition":
                run.current_step = next_after(step, run.context)
                run.steps_done = (run.steps_done or 0) + 1
                continue

            run.status = "failed"
            run.error = f"Unbekannter Schritt-Typ '{stype}'."
            run.completed_at = _now()
            await db.commit()
            return

        # 4) No next step → done.
        run.status = "completed"
        run.completed_at = _now()
        await db.commit()
    except Exception as e:  # noqa: BLE001 — never break the scheduler tick
        logger.warning("workflow advance_run failed for %s: %s", run.id, e)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
