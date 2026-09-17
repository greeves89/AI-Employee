"""Ein Disk-Quota-Stopp darf eine laufende Aufgabe nicht als "completed"
zuruecklassen (Issue #714).

Beleg vom 04.-07.09.2026: Container-Stopps wegen Speicherquote endeten Aufgaben
auf die Sekunde genau mit leerem oder mitten im Satz abgeschnittenem Ergebnis,
aber Status ``completed`` — der Agent war danach dauerhaft tot (Aufraeumen
braucht einen laufenden Container, genau den verhindert der Zustand), meldete
aber erledigte Arbeit, die nie fertig wurde. Der Stopp selbst stand nur im
Fehlerlog und fiel tagelang niemandem auf.

``_fail_running_tasks_and_alert`` muss deshalb VOR dem eigentlichen Stopp:
1. jede laufende Aufgabe des Agenten explizit auf ``failed`` setzen,
2. eine hochprioritaere Notification anlegen,
3. den Betreiber ueber Telegram alarmieren (DB-only Notifications erreichen
   ihn sonst nicht zuverlaessig, siehe duty_service._publish_telegram / #610).
"""

import unittest
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.agent import Agent, AgentState
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.services.disk_monitor import DiskMonitorService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ADiskQuotaStopFailsRunningTasksTests(unittest.IsolatedAsyncioTestCase):
    STATS: ClassVar[dict] = {"disk_usage_mb": 10262.0, "disk_limit_mb": 10240.0,
                              "disk_percent": 100.2, "disk_available_mb": 0.0}

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as db:
            db.add(Agent(id="a1", name="Voller Agent", state=AgentState.RUNNING,
                          container_id="c1", volume_name="workspace-a1", user_id="u1", config={}))
            db.add(Task(id="t1", title="laufend 1", prompt="x", status=TaskStatus.RUNNING,
                        agent_id="a1"))
            db.add(Task(id="t2", title="laufend 2", prompt="x", status=TaskStatus.RUNNING,
                        agent_id="a1"))
            db.add(Task(id="t3", title="schon fertig", prompt="x", status=TaskStatus.COMPLETED,
                        agent_id="a1"))
            db.add(Task(id="t4", title="anderer Agent", prompt="x", status=TaskStatus.RUNNING,
                        agent_id="a2"))
            await db.commit()

        self.redis = MagicMock()
        self.redis.client = AsyncMock()
        self.docker = MagicMock()
        self.monitor = DiskMonitorService(session_factory=self.Session, docker_service=self.docker,
                                           redis=self.redis)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _agent(self):
        async with self.Session() as db:
            return await db.get(Agent, "a1")

    async def _task(self, task_id):
        async with self.Session() as db:
            return await db.get(Task, task_id)

    async def test_laufende_aufgaben_des_agenten_werden_fehlgeschlagen(self):
        agent = await self._agent()
        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)
        t1 = await self._task("t1")
        t2 = await self._task("t2")
        self.assertEqual(t1.status, TaskStatus.FAILED)
        self.assertEqual(t2.status, TaskStatus.FAILED)
        self.assertIn("Speicherquote", t1.error)
        self.assertIsNotNone(t1.completed_at)

    async def test_bereits_fertige_aufgabe_bleibt_unangetastet(self):
        agent = await self._agent()
        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)
        t3 = await self._task("t3")
        self.assertEqual(t3.status, TaskStatus.COMPLETED)

    async def test_aufgabe_eines_anderen_agenten_bleibt_unangetastet(self):
        agent = await self._agent()
        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)
        t4 = await self._task("t4")
        self.assertEqual(t4.status, TaskStatus.RUNNING)

    async def test_eine_hochprioritaere_notification_wird_angelegt(self):
        agent = await self._agent()
        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)
        async with self.Session() as db:
            from sqlalchemy import select
            result = await db.execute(select(Notification).where(Notification.agent_id == "a1"))
            notif = result.scalar_one()
        self.assertEqual(notif.priority, "high")
        self.assertIn("Speicherquote", notif.title)
        self.assertEqual(notif.meta["tasks_failed"], 2)

    async def test_telegram_wird_alarmiert_nicht_nur_die_db(self):
        """Genau der Fehler aus #610: eine DB-only Notification erreicht den
        Betreiber nicht zuverlaessig."""
        agent = await self._agent()
        with patch("app.services.duty_service._publish_telegram", AsyncMock()) as telegram:
            await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)
        telegram.assert_awaited_once()
        titel, nachricht = telegram.call_args.args[1], telegram.call_args.args[2]
        self.assertIn("Speicherquote", titel)
        self.assertIn("Speicherquote", nachricht)

    async def test_kein_absturz_ohne_redis(self):
        """Der Betrieb ohne Redis (z. B. Testumgebung) darf den Stopp selbst
        nicht verhindern — nur der Alarm faellt dann aus."""
        self.monitor._redis = None
        agent = await self._agent()
        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)  # must not raise
        t1 = await self._task("t1")
        self.assertEqual(t1.status, TaskStatus.FAILED)

    async def test_stop_agent_ruft_es_vor_dem_eigentlichen_stopp_auf(self):
        """Die Reihenfolge ist der Kern des Fixes: nach dem Stopp gibt es
        keinen Weg zurueck, die Aufgaben muessen VORHER verbucht sein."""
        agent = await self._agent()
        aufruf_reihenfolge = []

        async def fake_fail(*a, **kw):
            aufruf_reihenfolge.append("fail_tasks")

        def fake_stop(*a, **kw):
            aufruf_reihenfolge.append("stop_container")

        self.docker.stop_container = fake_stop
        self.docker.cleanup_workspace_volume = MagicMock(return_value=None)
        with patch.object(self.monitor, "_fail_running_tasks_and_alert", fake_fail):
            await self.monitor._stop_agent(agent, self.STATS)

        self.assertEqual(aufruf_reihenfolge, ["fail_tasks", "stop_container"])


class ADiskQuotaCleanupResolvesTheDeadlockTests(unittest.IsolatedAsyncioTestCase):
    """Issue #714, Punkt 1: die eigentliche Verklemmung. Vorher gab es keinen
    Weg zurueck (Aufraeumen brauchte einen laufenden Container, genau den
    verhinderte der Stopp) — ``_cleanup_and_maybe_restart`` raeumt das Volume
    ueber einen Helfer-Container auf und startet den Agenten neu, wenn das
    reicht."""

    STATS: ClassVar[dict] = {"disk_usage_mb": 10262.0, "disk_limit_mb": 10240.0,
                              "disk_percent": 100.2, "disk_available_mb": 0.0}

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as db:
            db.add(Agent(id="a1", name="Voller Agent", state=AgentState.RUNNING,
                          container_id="c1", volume_name="workspace-a1", user_id="u1", config={}))
            await db.commit()

        self.redis = MagicMock()
        self.redis.client = AsyncMock()
        self.docker = MagicMock()
        self.monitor = DiskMonitorService(session_factory=self.Session, docker_service=self.docker,
                                           redis=self.redis)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _agent(self):
        async with self.Session() as db:
            return await db.get(Agent, "a1")

    async def test_cleanup_reaching_below_the_threshold_restarts_the_container(self):
        # 5000 / 10240 MB = 48.8 % — klar unter der 95%-Schwelle.
        self.docker.cleanup_workspace_volume = MagicMock(return_value=5000)
        agent = await self._agent()
        await self.monitor._cleanup_and_maybe_restart(agent, self.STATS)
        self.docker.start_container.assert_called_once_with("c1")

    async def test_cleanup_still_over_the_threshold_leaves_it_stopped(self):
        # 10100 / 10240 MB = 98.6 % — Aufraeumen half, reicht aber nicht.
        self.docker.cleanup_workspace_volume = MagicMock(return_value=10100)
        agent = await self._agent()
        await self.monitor._cleanup_and_maybe_restart(agent, self.STATS)
        self.docker.start_container.assert_not_called()

    async def test_a_failed_cleanup_run_leaves_it_stopped_without_raising(self):
        self.docker.cleanup_workspace_volume = MagicMock(return_value=None)
        agent = await self._agent()
        await self.monitor._cleanup_and_maybe_restart(agent, self.STATS)  # must not raise
        self.docker.start_container.assert_not_called()

    async def test_missing_volume_name_skips_cleanup_without_raising(self):
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            agent.volume_name = None
            await db.commit()
        agent = await self._agent()
        await self.monitor._cleanup_and_maybe_restart(agent, self.STATS)  # must not raise
        self.docker.cleanup_workspace_volume.assert_not_called()
        self.docker.start_container.assert_not_called()

    async def test_a_successful_recovery_is_notified(self):
        self.docker.cleanup_workspace_volume = MagicMock(return_value=5000)
        agent = await self._agent()
        await self.monitor._cleanup_and_maybe_restart(agent, self.STATS)
        async with self.Session() as db:
            from sqlalchemy import select
            result = await db.execute(select(Notification).where(Notification.agent_id == "a1"))
            notif = result.scalar_one()
        self.assertEqual(notif.priority, "normal")
        self.assertIn("aufgeraeumt", notif.title)

    async def test_a_successful_recovery_reaches_telegram(self):
        self.docker.cleanup_workspace_volume = MagicMock(return_value=5000)
        agent = await self._agent()
        with patch("app.services.duty_service._publish_telegram", AsyncMock()) as telegram:
            await self.monitor._cleanup_and_maybe_restart(agent, self.STATS)
        telegram.assert_awaited_once()

    async def test_stop_agent_runs_cleanup_after_stopping(self):
        self.docker.cleanup_workspace_volume = MagicMock(return_value=5000)
        agent = await self._agent()
        with patch.object(self.monitor, "_fail_running_tasks_and_alert", AsyncMock()):
            await self.monitor._stop_agent(agent, self.STATS)
        self.docker.cleanup_workspace_volume.assert_called_once_with("workspace-a1")
        self.docker.start_container.assert_called_once_with("c1")


if __name__ == "__main__":
    unittest.main()
