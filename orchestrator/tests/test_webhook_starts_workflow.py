"""Ein Webhook kann eine ganze Workflow-Kette auslösen (#392).

Motor, Zeitplan und Baukasten für Workflow-Ketten standen bereits — es fehlte
genau ein Auslöser: von aussen. Ohne ihn liess sich eine Kette nur manuell oder
per Cron starten, also nie als Reaktion auf ein Ereignis.

**Bewusst über den vorhandenen ``EventTrigger``**, nicht als zweiter Auslöser
daneben. Treffererkennung, Bedingungen, Sicherheitsprüfung der Nutzlast und die
Zähler gelten damit für beide Ziele gleich. Ein zweites Auslöser-System hätte
bedeutet, jede künftige Änderung an zwei Stellen zu pflegen — das Muster, an dem
in diesem Projekt schon mehrere Fehler hingen.

Die Nutzlast landet unter ``trigger`` im Lauf-Kontext und ist damit über die
**vorhandene** Platzhalter-Ersetzung ``{{trigger}}`` erreichbar.
"""

import json
import unittest
from types import SimpleNamespace

from app.api import webhooks
from app.services import workflow_engine


class _Db:
    """Nur so viel Datenbank, wie der Pfad wirklich anfasst."""

    def __init__(self, workflow):
        self._workflow = workflow
        self.added: list = []

    async def execute(self, _query):
        wf = self._workflow

        class _Res:
            def scalar_one_or_none(self):
                return wf

        return _Res()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass


def _workflow(enabled=True):
    return SimpleNamespace(
        id="wf1", enabled=enabled,
        definition={"start": "s1", "steps": {"s1": {"type": "agent_task"}}},
    )


def _trigger():
    return SimpleNamespace(id=7, name="Ticket eingegangen", workflow_id="wf1")


class StartFromTriggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_run_is_started(self):
        db = _Db(_workflow())
        out = await webhooks._start_workflow_from_trigger(
            db, _trigger(), "Bitte pruefen", {"ticket": 42}, "helpdesk", "created"
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["workflow_id"], "wf1")
        self.assertTrue(out["run_id"].startswith("wfr_"))

    async def test_the_payload_reaches_the_run(self):
        """Sonst wäre der Auslöser ein Startknopf ohne Inhalt — die Kette wüsste
        nicht, worauf sie reagiert."""
        db = _Db(_workflow())
        await webhooks._start_workflow_from_trigger(
            db, _trigger(), "Bitte pruefen", {"ticket": 42}, "helpdesk", "created"
        )
        run = db.added[0]
        self.assertIn("ticket", run.context["trigger"]["result"])
        self.assertEqual(run.context["trigger_source"]["result"], "helpdesk")
        self.assertEqual(run.context["trigger_event"]["result"], "created")
        self.assertEqual(run.context["trigger_prompt"]["result"], "Bitte pruefen")

    async def test_the_payload_is_reachable_via_the_existing_placeholder(self):
        """Keine zweite Ersetzungslogik: ``{{trigger}}`` muss mit der Mechanik
        funktionieren, die der Motor ohnehin hat."""
        db = _Db(_workflow())
        await webhooks._start_workflow_from_trigger(
            db, _trigger(), "p", {"ticket": 42}, "helpdesk", "created"
        )
        run = db.added[0]
        filled = workflow_engine.substitute("Vorgang: {{trigger}}", run.context)
        self.assertIn("42", filled)

    async def test_a_missing_workflow_falls_back_instead_of_vanishing(self):
        """Ein verschluckter Auslöser ist die Sorte Fehler, die niemand bemerkt,
        bis sie teuer wird. Lieber ein Auftrag als gar nichts."""
        self.assertIsNone(await webhooks._start_workflow_from_trigger(
            _Db(None), _trigger(), "p", {}, "s", "e"
        ))

    async def test_a_disabled_workflow_does_not_run(self):
        self.assertIsNone(await webhooks._start_workflow_from_trigger(
            _Db(_workflow(enabled=False)), _trigger(), "p", {}, "s", "e"
        ))

    async def test_a_huge_payload_is_capped(self):
        """Eine Nutzlast ohne Grenze wandert in jeden Prompt der Kette und sprengt
        das Kontextfenster."""
        db = _Db(_workflow())
        await webhooks._start_workflow_from_trigger(
            db, _trigger(), "p", {"x": "y" * 50_000}, "s", "e"
        )
        self.assertLessEqual(len(db.added[0].context["trigger"]["result"]), 8000)


class StartRunAcceptsContextTests(unittest.IsolatedAsyncioTestCase):
    """Der Motor musste dafür genau eine Kleinigkeit lernen."""

    async def test_context_defaults_to_empty(self):
        db = _Db(None)
        run = await workflow_engine.start_run(_workflow(), db)
        self.assertEqual(run.context, {})

    async def test_context_is_copied_not_shared(self):
        """Sonst schreibt der Lauf in das Wörterbuch des Aufrufers zurück."""
        db = _Db(None)
        given = {"a": {"result": "1"}}
        run = await workflow_engine.start_run(_workflow(), db, context=given)
        run.context["b"] = {"result": "2"}
        self.assertNotIn("b", given)


class TheTriggerCarriesTheTargetTests(unittest.TestCase):
    def test_the_model_has_the_column(self):
        from app.models.event_trigger import EventTrigger

        self.assertIn("workflow_id", EventTrigger.__table__.columns)

    def test_the_api_accepts_and_returns_it(self):
        from app.api.event_triggers import EventTriggerCreate, EventTriggerUpdate

        self.assertIn("workflow_id", EventTriggerCreate.model_fields)
        self.assertIn("workflow_id", EventTriggerUpdate.model_fields)


if __name__ == "__main__":
    unittest.main()
