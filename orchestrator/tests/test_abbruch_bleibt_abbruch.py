"""Ein abgebrochener Auftrag bleibt abgebrochen.

Nach dem Abbruch meldet der Agent sein Ende noch nach — unterbrochen oder sogar
„completed“. ``handle_task_completion`` ueberschrieb damit den Status: Der Abbruch
stand danach als fertig da (28.10.2026). Die Kosten des Laufs sollen aber gebucht
werden, und die Karte hat der Abbruch schon zurueckgelegt.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.task_router import TaskRouter
from app.models.task import TaskStatus


class _Redis:
    client = None


class AbbruchBleibtAbbruch(unittest.IsolatedAsyncioTestCase):
    def _router(self, task):
        ergebnis = MagicMock()
        ergebnis.scalar_one_or_none.return_value = task
        db = SimpleNamespace(execute=AsyncMock(return_value=ergebnis), commit=AsyncMock())
        return TaskRouter(db, _Redis(), MagicMock())

    async def _melden(self, status_vorher, gemeldet):
        task = SimpleNamespace(id="t1", status=status_vorher, agent_id="a1", result=None, error=None,
                               retain=True, metadata_={}, cost_usd=None)
        router = self._router(task)
        with patch("app.core.task_router.aktivitaet_vermerken", AsyncMock()), \
             patch("app.core.projekte.lauf_beendet", AsyncMock()) as lauf_beendet, \
             patch.object(TaskRouter, "_update_agent_metrics", AsyncMock()), \
             patch.object(TaskRouter, "_check_budget_thresholds", AsyncMock()), \
             patch("app.services.job_state.delete_job", AsyncMock()), \
             patch.object(TaskRouter, "_maybe_self_heal", AsyncMock(return_value=False)):
            try:
                await router.handle_task_completion({
                    "task_id": "t1", "agent_id": "a1", "status": gemeldet,
                    "result": "unterbrochen", "cost_usd": 17.11, "duration_ms": 1000,
                })
            except Exception:  # noqa: BLE001 — nur Status, Kosten und Karte interessieren
                pass
        return task, lauf_beendet

    async def test_spaete_erfolgsmeldung_ueberschreibt_den_abbruch_nicht(self):
        for gemeldet in ("completed", "failed"):
            with self.subTest(gemeldet=gemeldet):
                task, lauf_beendet = await self._melden(TaskStatus.CANCELLED, gemeldet)
                self.assertEqual(task.status, TaskStatus.CANCELLED)
                self.assertEqual(task.cost_usd, 17.11)        # Kosten trotzdem gebucht
                lauf_beendet.assert_not_called()               # Karte nicht ein zweites Mal

    async def test_laufender_auftrag_bekommt_den_gemeldeten_status(self):
        task, lauf_beendet = await self._melden(TaskStatus.RUNNING, "completed")
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        lauf_beendet.assert_awaited()


if __name__ == "__main__":
    unittest.main()
