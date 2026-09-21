"""Ein Speicherquoten-Stopp muss den Agenten auch als gestoppt VERMERKEN.

Aufgefallen am 21.09.2026 auf einer Anlage: In der Uebersicht stand ein Agent
auf "laeuft", sein Container war seit einer Viertelstunde beendet. Der
Quoten-Waechter stoppt den Container, schreibt einen ``stop_reason`` in die
Konfiguration und alarmiert — nur ``agent.state`` bleibt auf ``RUNNING``.

Das ist nicht bloss ein falsches Abzeichen:

* Der Waechter sucht sich seine Kandidaten ueber genau dieses Feld
  (``state in (RUNNING, IDLE, WORKING)``). Ein Agent, der laut Datenbank
  laeuft, aber keinen laufenden Container hat, wird in jedem Durchlauf erneut
  eingesammelt; dass es nicht in einer Schleife endet, liegt allein daran,
  dass die Belegungsmessung an einem beendeten Container scheitert — also am
  Zufall, nicht am Entwurf.
* Wer auf die Uebersicht schaut, sieht einen laufenden Agenten, der nicht
  antwortet, und sucht den Fehler an der falschen Stelle.

Gegenstueck: Reicht der Aufraeumlauf und der Container startet automatisch
wieder, muss der Zustand ebenso wieder auf ``RUNNING`` stehen.
"""

import unittest
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.agent import Agent, AgentState
from app.models.notification import Notification
from app.models.task import Task
from app.services.disk_monitor import DiskMonitorService


class DiskQuotaStoppVermerktZustand(unittest.IsolatedAsyncioTestCase):
    STATS: ClassVar[dict] = {"disk_usage_mb": 10416.0, "disk_limit_mb": 10240.0,
                             "disk_percent": 101.7, "disk_available_mb": 0.0}

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            for model in (Agent, Task, Notification):
                await conn.run_sync(model.metadata.create_all, tables=[model.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as db:
            db.add(Agent(id="a1", name="Voller Agent", state=AgentState.RUNNING,
                         container_id="c1", volume_name="workspace-a1",
                         user_id="u1", config={}))
            await db.commit()

        self.redis = MagicMock()
        self.redis.client = AsyncMock()
        self.docker = MagicMock()
        self.monitor = DiskMonitorService(session_factory=self.Session,
                                          docker_service=self.docker, redis=self.redis)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _zustand(self):
        async with self.Session() as db:
            return (await db.get(Agent, "a1")).state

    async def _agent(self):
        async with self.Session() as db:
            return await db.get(Agent, "a1")

    async def test_nach_dem_stopp_steht_der_agent_auf_gestoppt(self):
        # Aufraeumen bringt nichts — der Agent bleibt unten.
        self.docker.cleanup_workspace_volume.return_value = 10416.0
        await self.monitor._stop_agent(await self._agent(), self.STATS)

        self.docker.stop_container.assert_called_once_with("c1")
        self.assertEqual(
            await self._zustand(), AgentState.STOPPED,
            "Der Container ist beendet, die Datenbank meldet weiter 'laeuft' — "
            "die Uebersicht zeigt damit einen Agenten an, den es so nicht gibt.",
        )

    async def test_reicht_das_aufraeumen_laeuft_der_agent_wieder(self):
        self.docker.cleanup_workspace_volume.return_value = 4000.0  # ~39 %
        await self.monitor._stop_agent(await self._agent(), self.STATS)

        self.docker.start_container.assert_called_once_with("c1")
        self.assertEqual(
            await self._zustand(), AgentState.RUNNING,
            "Nach dem automatischen Neustart muss der Zustand wieder stimmen.",
        )
        # Nicht nur der Zustand — auch der Grund muss weg sein, sonst haengt
        # das Abzeichen "wegen Speicherquote gestoppt" an einem Agenten, der
        # laengst wieder laeuft.
        self.assertNotIn("stop_reason", (await self._agent()).config or {})

    async def test_der_stopp_grund_bleibt_erhalten(self):
        """Der Zustandsvermerk darf den Grund nicht ueberschreiben —
        Abzeichen und Anmelde-Hinweis lesen ihn."""
        self.docker.cleanup_workspace_volume.return_value = 10416.0
        await self.monitor._stop_agent(await self._agent(), self.STATS)

        agent = await self._agent()
        self.assertEqual((agent.config or {}).get("stop_reason", {}).get("type"), "disk_quota")


if __name__ == "__main__":
    unittest.main()
