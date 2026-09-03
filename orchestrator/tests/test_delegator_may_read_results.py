"""Wer delegiert, muss das Ergebnis auch abrufen dürfen.

Am 2026-08-12, 20:18 Uhr, stand beim Kunden im Chat:

    „Die vier Delegationen sind nach der Wartefrist weiterhin nicht mit
    verwertbaren Ergebnissen zurückgekommen. Der anschließende Statusabruf
    liefert für alle vier Aufträge derzeit ‚nicht abrufbar'. Das ist kein
    belastbarer Abschluss."

In der Datenbank standen zur selben Zeit alle vier Aufträge auf **COMPLETED**,
mit 10, 5, 4 und 5 Zügen echter Arbeit. Die Agenten hatten geliefert — der Lead
kam nur nicht an die Ergebnisse.

Ursache: ``_get_user_agent_ids`` gibt für einen Agenten ``[user.id]`` zurück. Er
sieht damit ausschliesslich seine EIGENEN Aufgaben. Ein Team-Lead legt per
``delegate_and_wait`` aber Aufträge für ANDERE an — und lief beim Abruf in ein
403. ``delegate_and_wait`` pollt genau diesen Endpunkt, ``get_tasks_status``
ebenfalls. Beide meldeten deshalb ewig „läuft noch" bzw. „nicht abrufbar".

Die Mandantentrennung bleibt unangetastet: Zugriff bekommt, wer die Aufgabe
**erzeugt** hat — nicht jeder Agent, und nicht agentenübergreifend.
"""

import unittest
from types import SimpleNamespace

from app.api import tasks as api


def _task(agent_id: str, creator: str | None):
    meta = {"created_by_agent": creator} if creator else {}
    return SimpleNamespace(id="t1", agent_id=agent_id, metadata_=meta)


def _agent(agent_id: str):
    return SimpleNamespace(id=agent_id, principal_type="agent", role="agent")


class DelegatorAccessTests(unittest.TestCase):
    def test_the_delegator_may_read_what_he_ordered(self):
        self.assertTrue(
            api._agent_delegated_this(_agent("lead"), _task("worker", "lead")),
            "Der Lead kommt sonst nie an das Ergebnis seiner eigenen Delegation "
            "— und meldet 'nicht abrufbar', obwohl die Arbeit fertig ist",
        )

    def test_a_stranger_may_not(self):
        """Die Trennung bleibt: fremde Aufgaben gehen niemanden etwas an."""
        self.assertFalse(
            api._agent_delegated_this(_agent("fremd"), _task("worker", "lead"))
        )

    def test_a_task_without_a_creator_grants_nothing(self):
        self.assertFalse(
            api._agent_delegated_this(_agent("lead"), _task("worker", None))
        )

    def test_a_human_does_not_slip_through_this_door(self):
        """Der Weg ist ausdrücklich nur für Agenten-Token — Menschen laufen
        weiter über die Besitzprüfung."""
        human = SimpleNamespace(id="lead", role="admin")
        self.assertFalse(api._agent_delegated_this(human, _task("worker", "lead")))

    def test_missing_metadata_is_no_crash(self):
        bare = SimpleNamespace(id="t1", agent_id="worker", metadata_=None)
        self.assertFalse(api._agent_delegated_this(_agent("lead"), bare))


def _full_task(agent_id: str, creator: str | None):
    """Eine Aufgabe, die durch ``TaskResponse`` passt — sonst prüfte der Test
    nur den Wächter und nicht den Endpunkt."""
    from datetime import datetime, timezone

    from app.models.task import TaskStatus

    meta = {"created_by_agent": creator} if creator else {}
    return SimpleNamespace(
        id="tp64b5q5v", title="Exportformat-Recherche", prompt="…",
        status=TaskStatus.COMPLETED, priority=5, agent_id=agent_id,
        model=None, result="Fertig: PDF als Standardexport.", error=None,
        cost_usd=0.1, input_tokens=1, output_tokens=1, duration_ms=1000,
        num_turns=10, parent_task_id=None, dry_run=False, original_prompt=None,
        created_at=datetime.now(timezone.utc), started_at=None,
        completed_at=datetime.now(timezone.utc), metadata_=meta,
    )


class TheEndpointActuallyLetsTheDelegatorThroughTests(unittest.IsolatedAsyncioTestCase):
    """Ein Wächter, den niemand ruft, hilft niemandem. Deshalb hier der echte
    Endpunkt, nicht nur die Hilfsfunktion."""

    class _Router:
        def __init__(self, task):
            self._task = task

        async def get_task(self, _task_id):
            return self._task

    async def test_the_lead_gets_the_result_instead_of_403(self):
        task = _full_task("worker", creator="lead")
        out = await api.get_task("tp64b5q5v", user=_agent("lead"), db=None,
                                 router_=self._Router(task))
        self.assertEqual(out.status.value, "completed")
        self.assertIn("PDF als Standardexport", out.result)

    async def test_a_stranger_still_gets_403(self):
        from fastapi import HTTPException

        task = _full_task("worker", creator="lead")

        async def _no_agents(_user, _db):
            return ["fremd"]          # sieht nur sich selbst

        orig = api._get_user_agent_ids
        api._get_user_agent_ids = _no_agents
        try:
            with self.assertRaises(HTTPException) as ctx:
                await api.get_task("tp64b5q5v", user=_agent("fremd"), db=None,
                                   router_=self._Router(task))
        finally:
            api._get_user_agent_ids = orig
        self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
