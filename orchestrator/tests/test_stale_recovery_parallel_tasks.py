"""Parallel laufende Aufgaben duerfen nicht als verschollen abgeraeumt werden.

Vorfall 2026-08-05: Die Aufgabe „Pitchdeck-Neugestaltung" (`tmuvl0046`) wurde als
`Task lost - agent stopped responding` beendet, waehrend der Agent daran arbeitete.
Sie lief parallel zum OpenWebUI-Watcher — und der ueberschrieb `current_task`.

Der Waechter stammt aus der Zeit, als ein Agent EINE Aufgabe nach der anderen
abarbeitete. Seit `MAX_PARALLEL_TASKS` (auf dem Pi: 8) laufen mehrere gleichzeitig,
gemeldet wird als `current_task` aber nur die zuletzt gestartete. Alle uebrigen
sahen aus wie „Agent antwortet nicht mehr". Je paralleler gearbeitet wird, desto
haeufiger trifft es: bei acht Aufgaben ist eine sichtbar, sieben sind Kandidaten.

Zwei unabhaengige Lebensbeweise statt einem: Der Agent fuehrt neben `current_task`
die vollstaendige Liste `active_sessions` — und unabhaengig davon schreibt eine
arbeitende Aufgabe weiter TaskSteps. Erst wenn BEIDES schweigt, ist sie wirklich tot.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.core.task_router import TaskRouter


def _router(status: dict) -> TaskRouter:
    r = TaskRouter.__new__(TaskRouter)
    r.redis = AsyncMock()
    r.redis.get_agent_status = AsyncMock(return_value=status)
    return r


class AgentClaimsTaskTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_named_current_task_counts(self):
        r = _router({"state": "working", "current_task": "tA"})
        self.assertTrue(await r._agent_claims_task("a1", "tA"))

    async def test_a_parallel_task_counts_too(self):
        """Der Kern des Fehlers: tB lief, wurde aber nicht als current_task genannt."""
        r = _router({
            "state": "working",
            "current_task": "tWatcher",
            "active_sessions": json.dumps(["tWatcher", "tB"]),
        })
        self.assertTrue(await r._agent_claims_task("a1", "tB"))

    async def test_an_unknown_task_does_not_count(self):
        r = _router({
            "state": "working",
            "current_task": "tWatcher",
            "active_sessions": json.dumps(["tWatcher"]),
        })
        self.assertFalse(await r._agent_claims_task("a1", "tGone"))

    async def test_an_idle_agent_claims_nothing(self):
        r = _router({"state": "idle", "current_task": "tA",
                     "active_sessions": json.dumps(["tA"])})
        self.assertFalse(await r._agent_claims_task("a1", "tA"))

    async def test_broken_session_list_is_not_a_free_pass(self):
        """Unlesbare Liste → kein Anspruch. Der TaskStep-Beweis greift dann noch."""
        r = _router({"state": "working", "current_task": "tX", "active_sessions": "{kaputt"})
        self.assertFalse(await r._agent_claims_task("a1", "tB"))

    async def test_missing_session_list_falls_back_to_current_task(self):
        """Aeltere Agenten melden die Liste noch nicht — die alte Regel muss greifen."""
        r = _router({"state": "working", "current_task": "tA"})
        self.assertTrue(await r._agent_claims_task("a1", "tA"))
        self.assertFalse(await r._agent_claims_task("a1", "tB"))


class TaskMovementTests(unittest.IsolatedAsyncioTestCase):
    """Der zweite, unabhaengige Lebensbeweis: schreibt sie noch Schritte?"""

    def _router_with_last_step(self, last):
        r = TaskRouter.__new__(TaskRouter)
        result = AsyncMock()
        result.scalar_one_or_none = lambda: last
        r.db = AsyncMock()
        r.db.execute = AsyncMock(return_value=result)
        return r

    async def test_a_recent_step_means_alive(self):
        now = datetime.now(timezone.utc)
        r = self._router_with_last_step(now)
        self.assertTrue(await r._task_moved_recently("tB", now - timedelta(minutes=10)))

    async def test_an_old_step_does_not(self):
        now = datetime.now(timezone.utc)
        r = self._router_with_last_step(now - timedelta(minutes=30))
        self.assertFalse(await r._task_moved_recently("tB", now - timedelta(minutes=10)))

    async def test_no_steps_at_all_does_not(self):
        now = datetime.now(timezone.utc)
        r = self._router_with_last_step(None)
        self.assertFalse(await r._task_moved_recently("tB", now - timedelta(minutes=10)))

    async def test_naive_timestamps_are_treated_as_utc(self):
        """SQLite/manche Treiber liefern tz-lose Werte — ein Vergleich wuerde sonst werfen."""
        now = datetime.now(timezone.utc)
        r = self._router_with_last_step(now.replace(tzinfo=None))
        self.assertTrue(await r._task_moved_recently("tB", now - timedelta(minutes=10)))


class RecoveryUsesBothProofsTests(unittest.TestCase):
    def test_the_running_branch_asks_both(self):
        """Ein Beweis allein luegt bei paralleler Arbeit — beide muessen gefragt werden."""
        import inspect
        src = inspect.getsource(TaskRouter.recover_stale_tasks)
        self.assertIn("_agent_claims_task", src)
        self.assertIn("_task_moved_recently", src)

    def test_no_raw_current_task_comparison_survives(self):
        """Genau dieser Vergleich war der Fehler — er darf nicht zurueckkehren."""
        import inspect
        src = inspect.getsource(TaskRouter)
        raw = [ln for ln in src.splitlines()
               if 'get("current_task") == task.id' in ln]
        self.assertEqual(raw, [], f"roher current_task-Vergleich zurueck: {raw}")


if __name__ == "__main__":
    unittest.main()
