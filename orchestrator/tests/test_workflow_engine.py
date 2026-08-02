"""Workflow engine (#392) — unit tests for the pure helpers + the state machine.

Pure: substitute() placeholder replace, eval_check() structured conditions,
next_after() branch resolution. State machine: advance_run() over agent_task /
condition / wait / end, with a mocked DB + TaskRouter.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock

from app.models.task import TaskStatus
from app.models.workflow import Workflow, WorkflowRun
from app.services import workflow_engine as we


class PureHelperTests(unittest.TestCase):
    def test_substitute(self):
        ctx = {"s1": {"result": "Hallo Welt"}, "s2": {"result": "OK"}}
        self.assertEqual(we.substitute("Sag: {{s1}} / {{s2}}", ctx), "Sag: Hallo Welt / OK")
        self.assertEqual(we.substitute("{{missing}}", ctx), "")
        self.assertEqual(we.substitute("kein platzhalter", ctx), "kein platzhalter")

    def test_eval_check(self):
        ctx = {"s1": {"result": "Ergebnis: OK, alles gut"}}
        self.assertTrue(we.eval_check({"step": "s1", "op": "contains", "value": "ok"}, ctx))
        self.assertFalse(we.eval_check({"step": "s1", "op": "contains", "value": "fehler"}, ctx))
        self.assertTrue(we.eval_check({"step": "s1", "op": "not_empty"}, ctx))
        self.assertTrue(we.eval_check({"step": "leer", "op": "is_empty"}, ctx))
        self.assertTrue(we.eval_check({"step": "s1", "op": "equals", "value": "Ergebnis: OK, alles gut"}, ctx))

    def test_next_after(self):
        cond = {"type": "condition", "check": {"step": "s1", "op": "contains", "value": "OK"}, "true": "yes", "false": "no"}
        self.assertEqual(we.next_after(cond, {"s1": {"result": "OK"}}), "yes")
        self.assertEqual(we.next_after(cond, {"s1": {"result": "nope"}}), "no")
        self.assertEqual(we.next_after({"type": "agent_task", "next": "s9"}, {}), "s9")


def _wf(steps, start="s1"):
    w = Workflow(id="wf1", name="t")
    w.definition = {"start": start, "steps": steps}
    return w


def _run(current, **kw):
    r = WorkflowRun(id="r1", workflow_id="wf1")
    r.status = "running"
    r.context = {}
    r.current_step = current
    r.current_task_id = None
    r.resume_at = None
    r.steps_done = 0
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _db(task=None):
    db = MagicMock()
    db.get = AsyncMock(return_value=task)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _router(task_id="task-1"):
    r = MagicMock()
    t = MagicMock(); t.id = task_id
    r.create_and_route_task = AsyncMock(return_value=t)
    return r


class AdvanceRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_task_creates_task_and_waits(self):
        wf = _wf({"s1": {"type": "agent_task", "prompt": "tu was", "next": None}})
        run = _run("s1")
        router = _router("task-42")
        await we.advance_run(run, wf, _db(), router)
        router.create_and_route_task.assert_awaited_once()
        self.assertEqual(run.current_task_id, "task-42")
        self.assertEqual(run.status, "running")   # still waiting on the task

    async def test_prompt_substitution_uses_context(self):
        wf = _wf({"s1": {"type": "agent_task", "prompt": "nutze {{s0}}", "next": None}})
        run = _run("s1", context={"s0": {"result": "DATEN"}})
        router = _router()
        await we.advance_run(run, wf, _db(), router)
        kwargs = router.create_and_route_task.call_args.kwargs
        self.assertIn("nutze DATEN", kwargs["prompt"])

    async def test_completed_task_advances_and_finishes(self):
        wf = _wf({"s1": {"type": "agent_task", "prompt": "x", "next": None}})
        run = _run("s1", current_task_id="task-1")   # waiting on task-1 at step s1
        task = MagicMock(); task.status = TaskStatus.COMPLETED; task.result = "fertig"; task.id = "task-1"
        await we.advance_run(run, wf, _db(task), _router())
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.context["s1"]["result"], "fertig")
        self.assertIsNone(run.current_task_id)

    async def test_failed_task_fails_run(self):
        wf = _wf({"s1": {"type": "agent_task", "prompt": "x", "next": None}})
        run = _run("s1", current_task_id="task-1")
        task = MagicMock(); task.status = TaskStatus.FAILED; task.id = "task-1"
        await we.advance_run(run, wf, _db(task), _router())
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.error)

    async def test_still_running_task_is_noop(self):
        wf = _wf({"s1": {"type": "agent_task", "prompt": "x", "next": None}})
        run = _run("s1", current_task_id="task-1")
        task = MagicMock(); task.status = TaskStatus.RUNNING; task.id = "task-1"
        await we.advance_run(run, wf, _db(task), _router())
        self.assertEqual(run.status, "running")
        self.assertEqual(run.current_task_id, "task-1")   # unchanged

    async def test_condition_branches_then_agent_task(self):
        wf = _wf({
            "s1": {"type": "condition", "check": {"step": "s0", "op": "contains", "value": "ja"}, "true": "s2", "false": None},
            "s2": {"type": "agent_task", "prompt": "los", "next": None},
        })
        run = _run("s1", context={"s0": {"result": "ja klar"}})
        router = _router("task-7")
        await we.advance_run(run, wf, _db(), router)
        # condition true -> s2 (agent_task) created
        self.assertEqual(run.current_task_id, "task-7")

    async def test_condition_false_ends_run(self):
        wf = _wf({
            "s1": {"type": "condition", "check": {"step": "s0", "op": "contains", "value": "ja"}, "true": "s2", "false": None},
            "s2": {"type": "agent_task", "prompt": "los", "next": None},
        })
        run = _run("s1", context={"s0": {"result": "nein"}})
        await we.advance_run(run, wf, _db(), _router())
        self.assertEqual(run.status, "completed")

    async def test_wait_sets_resume_and_returns(self):
        wf = _wf({"s1": {"type": "wait", "seconds": 60, "next": "s2"}, "s2": {"type": "agent_task", "prompt": "x", "next": None}})
        run = _run("s1")
        await we.advance_run(run, wf, _db(), _router())
        self.assertIsNotNone(run.resume_at)
        self.assertEqual(run.current_step, "s2")
        self.assertEqual(run.status, "running")

    async def test_unknown_step_fails(self):
        wf = _wf({"s1": {"type": "agent_task", "prompt": "x", "next": "ghost"}})
        run = _run("s1", current_task_id="task-1")
        task = MagicMock(); task.status = TaskStatus.COMPLETED; task.result = "r"; task.id = "task-1"
        await we.advance_run(run, wf, _db(task), _router())
        self.assertEqual(run.status, "failed")


if __name__ == "__main__":
    unittest.main()
