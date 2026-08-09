"""Selbstheilung von Ende zu Ende — gegen echtes SQL (#390).

``test_self_healing`` prueft die Regel allein: welcher Fehler wird wiederholt, mit
welcher Wartezeit. Das sagt nichts darueber, ob im Betrieb wirklich ein zweiter
Auftrag entsteht, ob die Faelligkeit greift und ob der Auftrag nach dem Abschicken
verschwindet, statt in jedem Takt erneut loszugehen.

Genau das ist hier gemeint: echter Task-Router, echte Modelle, echte Datenbank.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.task_router import TaskRouter
from app.models.agent import Agent, AgentState
from app.models.approval_rule import ApprovalRule
from app.models.command_approval import CommandApproval
from app.models.job_state import JobState
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.models.task_rating import TaskRating
from app.models.task_step import TaskStep


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


UTC = timezone.utc


class _FakeRedisClient:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, payload):  # noqa: ANN001
        self.published.append((channel, payload))

    async def lpush(self, *a, **kw):  # noqa: ANN001, D102
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


class SelfHealingE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            # Der Abschlusspfad beruehrt mehr als nur Tasks: Bewertung, Schrittspur
            # und Job-Zustand haengen mit dran. Fehlt eine dieser Tabellen, bricht
            # die Transaktion ab, BEVOR die Selbstheilung ueberhaupt drankommt —
            # der Test wuerde dann etwas anderes messen als er behauptet.
            for model in (Agent, Task, Notification, CommandApproval, ApprovalRule,
                          TaskRating, TaskStep, JobState):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, db, *, config=None):
        agent = Agent(
            id="a1", name="Buchhaltung", state=AgentState.RUNNING,
            user_id="u1", config=config or {},
        )
        db.add(agent)
        await db.commit()
        return agent

    def _router(self, db):
        return TaskRouter(db, self.redis, _FakeLoadBalancer())

    async def _fail(self, db, task, error):
        """Einen Fehlschlag so melden, wie es der Agent tut."""
        router = self._router(db)
        await router.handle_task_completion({
            "task_id": task.id,
            "agent_id": task.agent_id,
            "status": "failed",
            "error": error,
        })

    def _task(self, tid="t1", *, metadata=None):
        return Task(
            id=tid, title="Monatsbericht", prompt="Erstelle den Monatsbericht.",
            status=TaskStatus.RUNNING, agent_id="a1", model="claude-sonnet-5",
            metadata_=metadata or {},
        )

    # ── Der eigentliche Zweck ────────────────────────────────────────────────

    async def test_transient_failure_creates_a_waiting_retry(self):
        async with self.Session() as db:
            await self._seed(db)
            task = self._task()
            db.add(task)
            await db.commit()

            await self._fail(db, task, "Request timed out")

            retries = (await db.execute(
                select(Task).where(Task.id != "t1")
            )).scalars().all()
            self.assertEqual(len(retries), 1, "Es muss genau ein Wiederholungsauftrag entstehen")
            retry = retries[0]
            self.assertEqual(retry.status, TaskStatus.PENDING)
            self.assertEqual(retry.agent_id, "a1")
            self.assertEqual(retry.metadata_["heal_attempt"], 1)
            self.assertEqual(retry.metadata_["heal_of"], "t1")
            self.assertIn("heal_due_at", retry.metadata_)
            # Erster Versuch = unveraenderter Auftragstext.
            self.assertEqual(retry.prompt, task.prompt)

    async def test_no_failure_notification_while_a_retry_is_pending(self):
        """Sonst piept es dreimal fuer einen Zeitablauf, der sich von selbst
        erledigt."""
        async with self.Session() as db:
            await self._seed(db)
            task = self._task()
            db.add(task)
            await db.commit()

            await self._fail(db, task, "503 Service Unavailable")

            notifs = (await db.execute(select(Notification))).scalars().all()
            self.assertEqual(
                [n for n in notifs if n.type == "error"], [],
                "Ein geplanter neuer Versuch darf keine Fehlermeldung ausloesen",
            )

    async def test_permanent_failure_escalates_at_once(self):
        async with self.Session() as db:
            await self._seed(db)
            task = self._task()
            db.add(task)
            await db.commit()

            await self._fail(db, task, "401 Unauthorized: invalid api key")

            retries = (await db.execute(select(Task).where(Task.id != "t1"))).scalars().all()
            self.assertEqual(retries, [], "Ein dauerhafter Fehler darf nicht wiederholt werden")

            approvals = (await db.execute(select(CommandApproval))).scalars().all()
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0].meta["reason"], "permanent_error")
            self.assertEqual(approvals[0].task_id, "t1")

    async def test_exhausted_attempts_escalate_with_history(self):
        async with self.Session() as db:
            await self._seed(db, config={"self_healing": {"max_attempts": 1}})
            task = self._task(metadata={
                "heal_attempt": 1,
                "heal_strategy": "retry",
                "heal_of": "t0",
                "heal_history": [{"attempt": 0, "strategy": "original",
                                  "classification": "transient", "error": "timeout"}],
            })
            db.add(task)
            await db.commit()

            await self._fail(db, task, "timeout again")

            approvals = (await db.execute(select(CommandApproval))).scalars().all()
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0].meta["reason"], "self_healing_exhausted")
            self.assertGreaterEqual(approvals[0].meta["attempts"], 2)
            self.assertIn("timeout", approvals[0].description)

    async def test_dry_run_is_never_retried(self):
        """Ein Probelauf ist eine Vorschau, kein Auftrag."""
        async with self.Session() as db:
            await self._seed(db)
            task = self._task(metadata={"dry_run": True})
            db.add(task)
            await db.commit()

            await self._fail(db, task, "timeout")

            retries = (await db.execute(select(Task).where(Task.id != "t1"))).scalars().all()
            self.assertEqual(retries, [])

    # ── Die Uhr ──────────────────────────────────────────────────────────────

    async def test_retry_is_not_sent_before_it_is_due(self):
        async with self.Session() as db:
            await self._seed(db)
            db.add(Task(
                id="t2", title="x", prompt="y", status=TaskStatus.PENDING, agent_id="a1",
                metadata_={"heal_due_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat()},
            ))
            await db.commit()

            sent = await self._router(db).dispatch_due_retries()
            self.assertEqual(sent, 0)
            self.assertEqual(self.redis.pushed, [])

    async def test_due_retry_is_sent_once_and_not_again(self):
        """Bliebe die Faelligkeit stehen, ginge derselbe Auftrag in jedem Takt
        erneut los."""
        async with self.Session() as db:
            await self._seed(db)
            db.add(Task(
                id="t2", title="Monatsbericht (Versuch 2)", prompt="Erstelle den Monatsbericht.",
                status=TaskStatus.PENDING, agent_id="a1", model="claude-sonnet-5",
                metadata_={"heal_due_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            ))
            await db.commit()

            router = self._router(db)
            self.assertEqual(await router.dispatch_due_retries(), 1)
            self.assertEqual(len(self.redis.pushed), 1)
            agent_id, payload = self.redis.pushed[0]
            self.assertEqual(agent_id, "a1")
            self.assertEqual(json.loads(payload)["id"], "t2")

            task = await db.get(Task, "t2")
            await db.refresh(task)
            self.assertEqual(task.status, TaskStatus.QUEUED)
            self.assertNotIn("heal_due_at", task.metadata_ or {})

            self.assertEqual(await router.dispatch_due_retries(), 0)
            self.assertEqual(len(self.redis.pushed), 1)

    async def test_ordinary_pending_tasks_are_left_alone(self):
        """PENDING heisst sonst „kein Agent frei" — solche Auftraege duerfen nicht
        von der Selbstheilung abgeschickt werden."""
        async with self.Session() as db:
            await self._seed(db)
            db.add(Task(id="t3", title="x", prompt="y", status=TaskStatus.PENDING, agent_id="a1"))
            await db.commit()

            self.assertEqual(await self._router(db).dispatch_due_retries(), 0)
            self.assertEqual(self.redis.pushed, [])

    async def test_unreadable_due_date_does_not_break_the_tick(self):
        async with self.Session() as db:
            await self._seed(db)
            db.add(Task(
                id="t4", title="x", prompt="y", status=TaskStatus.PENDING, agent_id="a1",
                metadata_={"heal_due_at": "übermorgen"},
            ))
            db.add(Task(
                id="t5", title="x", prompt="y", status=TaskStatus.PENDING, agent_id="a1",
                metadata_={"heal_due_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            ))
            await db.commit()

            # Der kaputte Eintrag darf den gueltigen daneben nicht mitreissen.
            self.assertEqual(await self._router(db).dispatch_due_retries(), 1)

    async def test_retry_for_a_vanished_agent_is_not_sent(self):
        async with self.Session() as db:
            db.add(Task(
                id="t6", title="x", prompt="y", status=TaskStatus.PENDING, agent_id="weg",
                metadata_={"heal_due_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            ))
            await db.commit()

            self.assertEqual(await self._router(db).dispatch_due_retries(), 0)
            self.assertEqual(self.redis.pushed, [])

    async def test_second_retry_changes_the_prompt(self):
        async with self.Session() as db:
            await self._seed(db)
            task = self._task(metadata={
                "heal_attempt": 1,
                "heal_original_prompt": "Erstelle den Monatsbericht.",
                "heal_of": "t0",
            })
            db.add(task)
            await db.commit()

            await self._fail(db, task, "timeout")

            retry = (await db.execute(
                select(Task).where(Task.id != "t1")
            )).scalars().one()
            self.assertEqual(retry.metadata_["heal_attempt"], 2)
            self.assertIn("kleinere", retry.prompt)
            # Der urspruengliche Text bleibt erhalten — sonst waechst bei jedem
            # Versuch ein weiterer Hinweis oben drauf.
            self.assertEqual(
                retry.metadata_["heal_original_prompt"], "Erstelle den Monatsbericht."
            )


if __name__ == "__main__":
    unittest.main()
