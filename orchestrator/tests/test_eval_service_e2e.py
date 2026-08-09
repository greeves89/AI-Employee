"""Golden-Tests von Ende zu Ende — echter Task-Router, echtes SQL (#391).

Die Rechnung allein sagt nichts darueber, ob im Betrieb wirklich Auftraege
entstehen, ob die Antworten den richtigen Aufgaben zugeordnet werden und ob der
Lauf sich von selbst abschliesst. Genau dort liegen die Fehler, die man sonst erst
am falschen Wert merkt — und dann glaubt man ihm.
"""

import unittest
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.task_router import TaskRouter
from app.models.agent import Agent, AgentState
from app.models.approval_rule import ApprovalRule
from app.models.command_approval import CommandApproval
from app.models.eval_set import EvalRun, EvalSet
from app.models.job_state import JobState
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.task_step import TaskStep
from app.services import eval_service


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


class _FakeRedisClient:
    async def publish(self, *a, **kw):  # noqa: ANN001, D102
        return 1

    async def delete(self, *a, **kw):  # noqa: ANN001, D102
        return 1

    async def lrange(self, *a, **kw):  # noqa: ANN001, D102
        return []


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()
        self.pushed: list[tuple[str, str]] = []

    async def push_task(self, agent_id, payload):  # noqa: ANN001
        self.pushed.append((agent_id, payload))


class _FakeLoadBalancer:
    async def select_agent(self, priority=1):  # noqa: ANN001
        return None


ITEMS = [
    {"id": "ust", "title": "USt korrekt", "prompt": "Wie viel USt auf 100 €?",
     "expect_contains": ["19"], "weight": 3},
    {"id": "gruss", "title": "Grussformel", "prompt": "Schreibe eine Mail.",
     "expect_contains": ["Grüße"], "weight": 1},
]


class EvalServiceE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification, CommandApproval, ApprovalRule,
                          TaskRating, TaskStep, JobState, EvalSet, EvalRun):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _router(self, db):
        return TaskRouter(db, self.redis, _FakeLoadBalancer())

    async def _seed(self, db, items=None):
        db.add(Agent(id="a1", name="Buchhaltung", state=AgentState.RUNNING, user_id="u1", config={}))
        # `items or ITEMS` waere falsch: eine leere Liste ist hier eine ABSICHT,
        # kein „nicht angegeben" — genau der Fall, den ein Test unten prueft.
        eval_set = EvalSet(id="es1", name="Buchhaltung", role="Buchhaltung",
                           items=ITEMS if items is None else items,
                           version=1, user_id="u1")
        db.add(eval_set)
        await db.commit()
        return eval_set

    async def _answer(self, db, task_id, result, status="completed"):
        await self._router(db).handle_task_completion({
            "task_id": task_id, "agent_id": "a1", "status": status,
            "result": result, "error": None if status == "completed" else "kaputt",
        })

    async def _eval_tasks(self, db, run_id):
        rows = (await db.execute(select(Task))).scalars().all()
        return {
            (t.metadata_ or {}).get("eval_item_id"): t
            for t in rows if (t.metadata_ or {}).get("eval_run_id") == run_id
        }

    # ── Lauf anlegen ─────────────────────────────────────────────────────────

    async def test_run_creates_one_task_per_item(self):
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run = await eval_service.start_run(db, self._router(db), eval_set, "a1")

            self.assertEqual(run.total, 2)
            self.assertEqual(run.status, "running")
            self.assertEqual(run.set_version, 1)
            tasks = await self._eval_tasks(db, run.id)
            self.assertEqual(set(tasks), {"ust", "gruss"})
            self.assertEqual(len(self.redis.pushed), 2)

    async def test_eval_tasks_are_excluded_from_self_healing(self):
        """Eine Wiederholung gaebe dem Agenten einen zweiten Anlauf, den es im
        Betrieb nicht gab — der gemessene Wert waere besser als die Wirklichkeit."""
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run = await eval_service.start_run(db, self._router(db), eval_set, "a1")
            tasks = await self._eval_tasks(db, run.id)

            before = (await db.execute(select(Task))).scalars().all()
            await self._answer(db, tasks["ust"].id, None, status="failed")
            after = (await db.execute(select(Task))).scalars().all()

            self.assertEqual(len(before), len(after), "Es darf kein Wiederholungsauftrag entstehen")

    # ── Bewertung ────────────────────────────────────────────────────────────

    async def test_run_finalizes_once_every_answer_is_in(self):
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run = await eval_service.start_run(db, self._router(db), eval_set, "a1")
            tasks = await self._eval_tasks(db, run.id)

            await self._answer(db, tasks["ust"].id, "Das sind 19 Euro USt.")
            fresh = await db.get(EvalRun, run.id)
            await db.refresh(fresh)
            self.assertEqual(fresh.status, "running", "Ein Lauf ist erst mit ALLEN Antworten fertig")

            await self._answer(db, tasks["gruss"].id, "Viele Grüße, dein Agent")
            await db.refresh(fresh)
            self.assertEqual(fresh.status, "completed")
            self.assertEqual(fresh.score, 100.0)
            self.assertEqual(fresh.passed, 2)
            self.assertIsNotNone(fresh.completed_at)

    async def test_weighting_shows_up_in_the_score(self):
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run = await eval_service.start_run(db, self._router(db), eval_set, "a1")
            tasks = await self._eval_tasks(db, run.id)

            # Die schwere Aufgabe faellt durch, die leichte besteht → 1 von 4.
            await self._answer(db, tasks["ust"].id, "Keine Ahnung.")
            await self._answer(db, tasks["gruss"].id, "Viele Grüße")

            fresh = await db.get(EvalRun, run.id)
            await db.refresh(fresh)
            self.assertEqual(fresh.score, 25.0)

    async def test_failed_task_counts_as_not_passed(self):
        """Ein Agent, der abstuerzt, hat die Aufgabe nicht geloest. Sie zu
        ueberspringen wuerde den Wert schoenen."""
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run = await eval_service.start_run(db, self._router(db), eval_set, "a1")
            tasks = await self._eval_tasks(db, run.id)

            await self._answer(db, tasks["ust"].id, None, status="failed")
            await self._answer(db, tasks["gruss"].id, "Viele Grüße")

            fresh = await db.get(EvalRun, run.id)
            await db.refresh(fresh)
            self.assertEqual(fresh.status, "completed")
            self.assertEqual(fresh.passed, 1)
            self.assertEqual(fresh.score, 25.0)

    async def test_ordinary_tasks_are_untouched(self):
        async with self.Session() as db:
            await self._seed(db)
            db.add(Task(id="t9", title="echte Arbeit", prompt="x",
                        status=TaskStatus.RUNNING, agent_id="a1", metadata_={}))
            await db.commit()
            await self._answer(db, "t9", "fertig")

            runs = (await db.execute(select(EvalRun))).scalars().all()
            self.assertEqual(runs, [])

    # ── Grundlinie und Gatter ────────────────────────────────────────────────

    async def _complete_run(self, db, eval_set, answers):
        run = await eval_service.start_run(db, self._router(db), eval_set, "a1")
        tasks = await self._eval_tasks(db, run.id)
        for item_id, answer in answers.items():
            await self._answer(db, tasks[item_id].id, answer)
        fresh = await db.get(EvalRun, run.id)
        await db.refresh(fresh)
        return fresh

    async def test_baseline_is_the_best_run_not_the_last(self):
        """Sonst koennte man eine Verschlechterung festschreiben, indem man zweimal
        schlecht laeuft."""
        async with self.Session() as db:
            eval_set = await self._seed(db)
            first = await self._complete_run(db, eval_set, {"ust": "19", "gruss": "Grüße"})
            self.assertEqual(first.score, 100.0)

            second = await self._complete_run(db, eval_set, {"ust": "keine Ahnung", "gruss": "Grüße"})
            self.assertEqual(second.baseline_score, 100.0)
            self.assertTrue(second.regression)

            third = await self._complete_run(db, eval_set, {"ust": "keine", "gruss": "Grüße"})
            self.assertEqual(
                third.baseline_score, 100.0,
                "Die Grundlinie darf nicht auf den schlechten Lauf absacken",
            )

    async def test_first_run_becomes_the_baseline_without_blocking(self):
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run = await self._complete_run(db, eval_set, {"ust": "19", "gruss": "Grüße"})
            self.assertIsNone(run.baseline_score)
            self.assertFalse(run.regression)

            agent = await db.get(Agent, "a1")
            decision = await eval_service.gate_for_agent(db, agent)
            self.assertTrue(decision["allowed"])

    async def test_gate_blocks_after_a_regression(self):
        async with self.Session() as db:
            eval_set = await self._seed(db)
            await self._complete_run(db, eval_set, {"ust": "19", "gruss": "Grüße"})
            await self._complete_run(db, eval_set, {"ust": "keine", "gruss": "nix"})

            agent = await db.get(Agent, "a1")
            decision = await eval_service.gate_for_agent(db, agent)
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["reason"], "regression")

    async def test_gate_without_any_run_lets_updates_through(self):
        async with self.Session() as db:
            await self._seed(db)
            agent = await db.get(Agent, "a1")
            self.assertTrue((await eval_service.gate_for_agent(db, agent))["allowed"])

    async def test_gate_can_demand_a_run(self):
        async with self.Session() as db:
            await self._seed(db)
            agent = await db.get(Agent, "a1")
            agent.config = {"eval_gate": {"require_run": True}}
            await db.commit()
            decision = await eval_service.gate_for_agent(db, agent)
            self.assertFalse(decision["allowed"])
            self.assertEqual(decision["reason"], "no_run")

    async def test_gate_can_be_switched_off(self):
        async with self.Session() as db:
            eval_set = await self._seed(db)
            await self._complete_run(db, eval_set, {"ust": "19", "gruss": "Grüße"})
            await self._complete_run(db, eval_set, {"ust": "keine", "gruss": "nix"})

            agent = await db.get(Agent, "a1")
            agent.config = {"eval_gate": {"enabled": False}}
            await db.commit()
            self.assertTrue((await eval_service.gate_for_agent(db, agent))["allowed"])

    async def test_changed_set_version_is_recorded_with_the_run(self):
        """Aendert jemand spaeter eine Aufgabe, bleibt der alte Lauf deutbar."""
        async with self.Session() as db:
            eval_set = await self._seed(db)
            run1 = await eval_service.start_run(db, self._router(db), eval_set, "a1")
            eval_set.version = 2
            await db.commit()
            run2 = await eval_service.start_run(db, self._router(db), eval_set, "a1")
            self.assertEqual(run1.set_version, 1)
            self.assertEqual(run2.set_version, 2)

    async def test_empty_set_cannot_be_run(self):
        async with self.Session() as db:
            eval_set = await self._seed(db, items=[])
            with self.assertRaises(ValueError):
                await eval_service.start_run(db, self._router(db), eval_set, "a1")


if __name__ == "__main__":
    unittest.main()
