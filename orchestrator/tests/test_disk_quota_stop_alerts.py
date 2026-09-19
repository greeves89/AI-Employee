"""Ende-zu-Ende: was nach einem Speicherquote-Stopp beim Nutzer ankommt (#830).

Nutzeranfrage, nachdem ein Agent durch wiederholte Disk-Quota-Stopps unbrauchbar
war: eine Meldung im Backend-Log reicht nicht — Login-Popup, Warn-Badge in der
Agenten-Uebersicht, iOS-Push UND der Agent selbst muessen es wissen. Diese Tests
pruefen den durablen Zustand (``agent.config["stop_reason"]``), der Popup und
Badge speist, dessen Aufraeumen bei Erholung/manuellem Neustart, und dass der
Push-Kanal (bisher nur Telegram) tatsaechlich mitgerufen wird.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.agent_manager import AgentManager
from app.models.agent import Agent, AgentState
from app.models.notification import Notification
from app.models.task import Task
from app.services.disk_monitor import DiskMonitorService


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_, compiler, **kw):  # noqa: ANN001
    return "JSON"


class _FakeRedisClient:
    async def publish(self, *a, **kw):  # noqa: ANN001, D102
        pass


class _FakeRedis:
    def __init__(self):
        self.client = _FakeRedisClient()


class DiskQuotaStopAlertsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Notification, Task):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.redis = _FakeRedis()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, db, *, config=None):
        agent = Agent(
            id="a1", name="DEV_Prod Agent", state=AgentState.RUNNING,
            user_id="u1", container_id="c1", config=config or {},
        )
        db.add(agent)
        await db.commit()
        return agent

    def _monitor(self):
        return DiskMonitorService(session_factory=self.Session, docker_service=MagicMock(), redis=self.redis)

    async def test_stop_records_a_readable_reason_on_the_agent(self):
        """Die Uebersichtsseite und das Login-Popup lesen agent.config direkt —
        ohne Extra-Abfrage gegen /notifications muessen sie den Grund sehen."""
        async with self.Session() as db:
            agent = await self._seed(db)
            monitor = self._monitor()
            with patch("app.core.push.push_to_user", new_callable=AsyncMock):
                await monitor._fail_running_tasks_and_alert(
                    agent, {"disk_usage_mb": 9750, "disk_limit_mb": 10240, "disk_percent": 95.2},
                )

            refreshed = await db.get(Agent, "a1")
            await db.refresh(refreshed)
            reason = refreshed.config.get("stop_reason")
            self.assertIsNotNone(reason)
            self.assertEqual(reason["type"], "disk_quota")
            self.assertEqual(reason["disk_percent"], 95.2)
            self.assertIn("Speicherquote", reason["message"])

    async def test_stop_pushes_to_the_owning_user_not_only_telegram(self):
        """#610 wurde fuer Telegram umgangen, aber Push (APNs/Web) nie
        nachgezogen — disk_monitor rief push_to_user bisher gar nicht auf."""
        async with self.Session() as db:
            agent = await self._seed(db)
            monitor = self._monitor()
            with patch("app.core.push.push_to_user", new_callable=AsyncMock) as mock_push:
                await monitor._fail_running_tasks_and_alert(
                    agent, {"disk_usage_mb": 9750, "disk_limit_mb": 10240, "disk_percent": 95.2},
                )
            mock_push.assert_awaited_once()
            args = mock_push.await_args
            self.assertEqual(args.args[1], "u1")  # user_id
            self.assertEqual(args.kwargs["data"]["type"], "disk_quota_stop")
            self.assertEqual(args.kwargs["data"]["agent_id"], "a1")

    async def test_stop_without_owner_does_not_push(self):
        async with self.Session() as db:
            agent = await self._seed(db)
            agent.user_id = None
            await db.commit()
            monitor = self._monitor()
            with patch("app.core.push.push_to_user", new_callable=AsyncMock) as mock_push:
                await monitor._fail_running_tasks_and_alert(
                    agent, {"disk_usage_mb": 9750, "disk_limit_mb": 10240, "disk_percent": 95.2},
                )
            mock_push.assert_not_awaited()

    async def test_recovery_clears_the_stop_reason(self):
        async with self.Session() as db:
            agent = await self._seed(db, config={
                "stop_reason": {"type": "disk_quota", "message": "x", "detail": "y",
                                 "disk_percent": 96.0, "at": "2026-09-19T09:00:00+00:00"},
            })
            monitor = self._monitor()
            await monitor._notify_recovered(agent, used_mb=5000, limit_mb=10240, percent=48.8)

            refreshed = await db.get(Agent, "a1")
            await db.refresh(refreshed)
            self.assertNotIn("stop_reason", refreshed.config)

    async def test_manual_restart_clears_the_stop_reason(self):
        """Wer den Agenten nach eigenem Aufraeumen selbst neu startet (statt auf
        den naechsten Ueberwachungslauf zu warten), soll das Badge sofort los."""
        async with self.Session() as db:
            await self._seed(db, config={
                "stop_reason": {"type": "disk_quota", "message": "x", "detail": "y",
                                 "disk_percent": 96.0, "at": "2026-09-19T09:00:00+00:00"},
            })

            docker = MagicMock()
            docker.get_container_status.return_value = "exited"
            manager = AgentManager(db, docker, MagicMock())
            manager.refresh_instructions = AsyncMock(return_value=True)
            manager._publish_event = AsyncMock()

            await manager.start_agent("a1")

            refreshed = await db.get(Agent, "a1")
            await db.refresh(refreshed)
            self.assertNotIn("stop_reason", refreshed.config)
            self.assertEqual(refreshed.state, AgentState.RUNNING)


if __name__ == "__main__":
    unittest.main()
