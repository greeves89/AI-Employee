"""Ein Sentinel-Stopp darf eine laufende Aufgabe nicht auf RUNNING stehen
lassen (Issue #855).

Beleg vom 24.-25.09.2026: der Fix zu #838 (Sperre und Watchdog messen "haengt"
mit derselben Schwelle, `df598a5b`) war gemergt, aber 138 Zeitplan-Laeufe (62
davon nach dem Merge) wurden trotzdem ersatzlos uebersprungen. Ursache:
`agent_duty.assess` liest den Zustand "blocked" ausschliesslich aus
`find_stale_tasks` — einer laufenden Aufgabe mit altem `updated_at`. Ein
Sentinel-Stopp hielt bislang nur den Container an und liess die Aufgabe(n)
auf RUNNING stehen; bis der Watchdog sie nach `watchdog_stale_task_minutes`
(Standard 180 min) als stale erkennt, gilt der Agent als blockiert und jeder
faellige Lauf wird uebersprungen.

`_fail_running_tasks` schliesst deshalb — genau wie
`disk_monitor._fail_running_tasks_and_alert` (#714) fuer den Speicherquote-
Pfad — die laufenden Aufgaben des gestoppten Agenten selbst, sobald der Stopp
gelingt. Damit macht die Watchdog-Schwelle fuer DIESE Klasse Vorfall (Sentinel
stoppt einen Agenten) keinen Unterschied mehr.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.agent import Agent, AgentState
from app.models.audit_log import AuditLog
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.services.sentinel_service import SentinelService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ASentinelStopFailsRunningTasksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification, AuditLog):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as db:
            db.add(Agent(id="a1", name="Verdaechtiger Agent", state=AgentState.RUNNING,
                          container_id="c1", user_id="u1", config={}))
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
        self.service = SentinelService(self.redis)
        self._session_patch = patch("app.db.session.async_session_factory", self.Session)
        self._session_patch.start()

    async def asyncTearDown(self):
        self._session_patch.stop()
        await self.engine.dispose()

    async def _task(self, task_id):
        async with self.Session() as db:
            return await db.get(Task, task_id)

    async def test_laufende_aufgaben_werden_fehlgeschlagen(self):
        await self.service._fail_running_tasks("a1", "geheimnis_im_auszug")
        t1 = await self._task("t1")
        t2 = await self._task("t2")
        self.assertEqual(t1.status, TaskStatus.FAILED)
        self.assertEqual(t2.status, TaskStatus.FAILED)
        self.assertIn("Sentinel", t1.error)
        self.assertIn("geheimnis_im_auszug", t1.error)
        self.assertIsNotNone(t1.completed_at)
        self.assertTrue(t1.notified)
        self.assertIsNotNone(t1.evict_after)

    async def test_bereits_fertige_aufgabe_bleibt_unangetastet(self):
        await self.service._fail_running_tasks("a1", "grund")
        t3 = await self._task("t3")
        self.assertEqual(t3.status, TaskStatus.COMPLETED)

    async def test_aufgabe_eines_anderen_agenten_bleibt_unangetastet(self):
        await self.service._fail_running_tasks("a1", "grund")
        t4 = await self._task("t4")
        self.assertEqual(t4.status, TaskStatus.RUNNING)

    async def test_kein_absturz_ohne_laufende_aufgaben(self):
        await self.service._fail_running_tasks("kein-solcher-agent", "grund")  # must not raise

    async def test_ein_db_fehler_darf_nicht_nach_aussen_dringen(self):
        """`_fail_running_tasks` steht in `_stop_agent`s try-Block direkt nach
        dem erfolgreichen Anhalten — ein Fehler hier darf den bereits
        erfolgreich vermerkten Stopp nicht in einen falschen Fehlerzustand
        umdeuten."""
        with patch("app.db.session.async_session_factory", side_effect=RuntimeError("db weg")):
            await self.service._fail_running_tasks("a1", "grund")  # must not raise

    async def test_stop_agent_schliesst_laufende_aufgaben_nach_erfolgreichem_stopp(self):
        """End-to-end durch `_stop_agent`: Redis-ACL an, `AgentManager.stop_agent`
        gelingt (gemockt) -> die laufenden Aufgaben muessen FAILED sein, und
        das Pruefprotokoll muss trotzdem `outcome=success` vermerken."""
        with patch("app.config.settings.redis_acl_enabled", True), \
             patch("app.core.agent_manager.AgentManager.stop_agent", AsyncMock()):
            self.service.docker = MagicMock()
            await self.service._stop_agent("a1", "prompt_injection")

        t1 = await self._task("t1")
        t2 = await self._task("t2")
        self.assertEqual(t1.status, TaskStatus.FAILED)
        self.assertEqual(t2.status, TaskStatus.FAILED)

        async with self.Session() as db:
            from sqlalchemy import select
            result = await db.execute(select(AuditLog).where(AuditLog.agent_id == "a1"))
            log = result.scalar_one()
        self.assertEqual(log.outcome, "success")

    async def test_stop_agent_scheitert_taeuscht_kein_faelschliches_fehlschlagen_der_aufgaben_vor(self):
        """Schlaegt das Anhalten selbst fehl (kein Docker-Dienst), bleiben die
        Aufgaben unangetastet — der Watchdog-Rueckfall greift dann weiter,
        statt eine Aufgabe als fehlgeschlagen zu verbuchen, obwohl der Agent
        moeglicherweise weiterlaeuft."""
        with patch("app.config.settings.redis_acl_enabled", True):
            self.service.docker = None  # loest RuntimeError in _stop_agent aus
            await self.service._stop_agent("a1", "prompt_injection")

        t1 = await self._task("t1")
        self.assertEqual(t1.status, TaskStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
