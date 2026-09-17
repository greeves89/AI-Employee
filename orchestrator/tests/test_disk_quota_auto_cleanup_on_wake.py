"""Ein wegen Speicherquote gestoppter Agent muss sich beim naechsten Wecken
selbst so weit aufraeumen koennen, dass er nicht sofort wieder stirbt (Issue
#714, Punkt 1: "der Agent kann sich nicht selbst befreien").

Vorher endete ``AgentManager.start_agent`` nach einem Quota-Stopp genauso wie
jeder andere Start: Container hochfahren, ``state = RUNNING``, fertig. Die
Quote hatte sich waehrend des Stopps nicht von selbst geaendert — die naechste
zugestellte Aufgabe (``ensure_agent_running`` weckt jeden gestoppten Agenten,
siehe #632) startete also mit voller Platte und starb wieder mitten im Satz.

Zwei Teile:
1. ``DiskMonitorService._fail_running_tasks_and_alert`` markiert den Agenten
   (``agent.config["disk_quota_stopped"]``), damit der Grund des Stopps beim
   Wecken bekannt ist.
2. ``AgentManager.start_agent`` sieht diese Markierung, versucht VOR dem
   normalen Hochfahren eine begrenzte, unbedenkliche Aufraeumung (dieselben
   Pfade, die die eigene Warnung dem Agenten ohnehin empfiehlt) und laesst den
   Agenten nur dann als RUNNING gelten, wenn die Quote danach wieder unter der
   Stopp-Schwelle liegt. Reicht die Aufraeumung nicht, bleibt er angehalten,
   statt eine Aufgabe anzunehmen, die ohnehin sofort wieder verhungert.
"""

import unittest
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

from app.core.agent_manager import AgentManager
from app.models.agent import Agent, AgentState
from app.models.notification import Notification
from app.models.task import Task, TaskStatus
from app.services.disk_monitor import DiskMonitorService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _DbBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()


class AMarkiertDenGrundDesStopps(_DbBase):
    """Teil 1: disk_monitor markiert den Agenten beim Stopp."""

    STATS: ClassVar[dict] = {"disk_usage_mb": 10262.0, "disk_limit_mb": 10240.0,
                              "disk_percent": 100.2, "disk_available_mb": 0.0}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.Session() as db:
            db.add(Agent(id="a1", name="Voller Agent", state=AgentState.RUNNING,
                          container_id="c1", user_id="u1", config={}))
            await db.commit()
        self.redis = MagicMock()
        self.redis.client = AsyncMock()
        self.docker = MagicMock()
        self.monitor = DiskMonitorService(session_factory=self.Session, docker_service=self.docker,
                                           redis=self.redis)

    async def test_agent_wird_als_disk_quota_gestoppt_markiert(self):
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)

        async with self.Session() as db:
            reloaded = await db.get(Agent, "a1")
        self.assertTrue(reloaded.config.get("disk_quota_stopped"))

    async def test_bestehende_config_werte_bleiben_erhalten(self):
        """Reine Ergaenzung, kein Ersetzen — sonst gingen andere Einstellungen
        (z. B. workspace_size_gb) bei jedem Quota-Stopp verloren."""
        async with self.Session() as db:
            agent = await db.get(Agent, "a1")
            agent.config = {"workspace_size_gb": 20}
            await db.commit()
            agent = await db.get(Agent, "a1")

        await self.monitor._fail_running_tasks_and_alert(agent, self.STATS)

        async with self.Session() as db:
            reloaded = await db.get(Agent, "a1")
        self.assertEqual(reloaded.config.get("workspace_size_gb"), 20)
        self.assertTrue(reloaded.config.get("disk_quota_stopped"))


def _agent_manager(docker) -> AgentManager:
    redis = MagicMock()
    redis.client = None  # kein Redis in diesem Test -> _publish_event ist ein No-Op
    return AgentManager(db=AsyncMock(), docker=docker, redis=redis)


class ADerAutomatischeAufraeumSchritt(unittest.IsolatedAsyncioTestCase):
    """Teil 2: ``_auto_cleanup_after_disk_quota_stop`` isoliert getestet."""

    def _agent(self, **overrides):
        agent = Agent(id="a1", name="Voller Agent", state=AgentState.STOPPED,
                       container_id="c1", user_id="u1", config={"disk_quota_stopped": True})
        for k, v in overrides.items():
            setattr(agent, k, v)
        return agent

    async def test_raeumt_nur_cache_tmp_und_logs_auf(self):
        docker = MagicMock()
        docker.exec_in_container.return_value = (0, "")
        docker.get_workspace_disk_usage.return_value = {
            "disk_usage_mb": 100.0, "disk_limit_mb": 10240.0,
            "disk_percent": 1.0, "disk_available_mb": 10140.0,
        }
        manager = _agent_manager(docker)
        agent = self._agent()

        ok = await manager._auto_cleanup_after_disk_quota_stop(agent)

        self.assertTrue(ok)
        docker.exec_in_container.assert_called_once()
        container_id, cmd = docker.exec_in_container.call_args.args
        self.assertEqual(container_id, "c1")
        befehl = cmd[-1]
        self.assertIn("/workspace/data/cache", befehl)
        self.assertIn("/workspace/tmp", befehl)
        self.assertIn("*.log", befehl)
        # Keine Repos/Review-Checkouts anfassen — das ist bewusst eine
        # Architekturentscheidung, die die Aufraeumung hier NICHT trifft.
        self.assertNotIn("git", befehl)
        self.assertNotIn(".git", befehl)

    async def test_loescht_die_markierung_wenn_die_quote_danach_wieder_passt(self):
        docker = MagicMock()
        docker.exec_in_container.return_value = (0, "")
        docker.get_workspace_disk_usage.return_value = {
            "disk_usage_mb": 100.0, "disk_limit_mb": 10240.0,
            "disk_percent": 1.0, "disk_available_mb": 10140.0,
        }
        manager = _agent_manager(docker)
        agent = self._agent()

        ok = await manager._auto_cleanup_after_disk_quota_stop(agent)

        self.assertTrue(ok)
        self.assertNotIn("disk_quota_stopped", agent.config)

    async def test_reicht_die_aufraeumung_nicht_bleibt_der_agent_angehalten(self):
        """Der eigentliche Vorfall (#714): die groessten Fresser waren verwaiste
        Review-Checkouts, keine Caches. Reine Cache-Aufraeumung reicht dann
        nicht — der Agent darf keine Aufgabe annehmen, die sofort wieder
        verhungert."""
        docker = MagicMock()
        docker.exec_in_container.return_value = (0, "")
        docker.get_workspace_disk_usage.return_value = {
            "disk_usage_mb": 10200.0, "disk_limit_mb": 10240.0,
            "disk_percent": 99.6, "disk_available_mb": 40.0,
        }
        manager = _agent_manager(docker)
        agent = self._agent()

        ok = await manager._auto_cleanup_after_disk_quota_stop(agent)

        self.assertFalse(ok)
        docker.stop_container.assert_called_once_with("c1")
        self.assertEqual(agent.state, AgentState.STOPPED)
        self.assertTrue(agent.config.get("disk_quota_stopped"))

    async def test_ein_fehlschlagender_exec_bricht_nicht_ab(self):
        docker = MagicMock()
        docker.exec_in_container.side_effect = RuntimeError("Container weg")
        manager = _agent_manager(docker)
        agent = self._agent()

        ok = await manager._auto_cleanup_after_disk_quota_stop(agent)

        self.assertFalse(ok)
        docker.get_workspace_disk_usage.assert_not_called()


class BStartAgentRuftDieAufraeumungAuf(unittest.IsolatedAsyncioTestCase):
    """Teil 2b: ``start_agent`` selbst verdrahtet die Aufraeumung korrekt."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Agent.metadata.create_all, tables=[Agent.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _add_agent(self, **kw):
        async with self.Session() as db:
            agent = Agent(id="a1", name="Agent", state=AgentState.STOPPED,
                           container_id="c1", user_id="u1", **kw)
            db.add(agent)
            await db.commit()

    async def test_ohne_markierung_bleibt_start_agent_unveraendert(self):
        await self._add_agent(config={})
        docker = MagicMock()
        docker.start_container.return_value = None
        docker.get_workspace_disk_usage.return_value = {"disk_percent": 1.0}
        async with self.Session() as db:
            manager = AgentManager(db=db, docker=docker, redis=MagicMock(client=None))
            agent = await manager.start_agent("a1")

        self.assertEqual(agent.state, AgentState.RUNNING)
        docker.exec_in_container.assert_not_called()

    async def test_mit_markierung_raeumt_start_agent_zuerst_auf(self):
        await self._add_agent(config={"disk_quota_stopped": True})
        docker = MagicMock()
        docker.start_container.return_value = None
        docker.exec_in_container.return_value = (0, "")
        docker.get_workspace_disk_usage.return_value = {
            "disk_usage_mb": 100.0, "disk_limit_mb": 10240.0,
            "disk_percent": 1.0, "disk_available_mb": 10140.0,
        }
        async with self.Session() as db:
            manager = AgentManager(db=db, docker=docker, redis=MagicMock(client=None))
            agent = await manager.start_agent("a1")

        docker.exec_in_container.assert_called_once()
        self.assertEqual(agent.state, AgentState.RUNNING)
        self.assertNotIn("disk_quota_stopped", agent.config)

    async def test_reicht_die_aufraeumung_nicht_bleibt_der_agent_gestoppt(self):
        await self._add_agent(config={"disk_quota_stopped": True})
        docker = MagicMock()
        docker.start_container.return_value = None
        docker.exec_in_container.return_value = (0, "")
        docker.get_workspace_disk_usage.return_value = {
            "disk_usage_mb": 10230.0, "disk_limit_mb": 10240.0,
            "disk_percent": 99.9, "disk_available_mb": 10.0,
        }
        async with self.Session() as db:
            manager = AgentManager(db=db, docker=docker, redis=MagicMock(client=None))
            agent = await manager.start_agent("a1")

        self.assertEqual(agent.state, AgentState.STOPPED)
        docker.stop_container.assert_called_once_with("c1")
        # refresh_instructions (Anleitung nachziehen) darf hier NICHT laufen —
        # ein weiterhin voller Agent bekommt keine neue Aufgabe angeboten.
        docker.write_file_in_container.assert_not_called()


if __name__ == "__main__":
    unittest.main()
