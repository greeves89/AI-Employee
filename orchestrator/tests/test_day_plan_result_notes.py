"""Das ERGEBNIS eines Tagesplan-Blocks liess sich nie eintragen (Issue #717).

`PATCH /api/v1/day-plan/{item_id}` wies jede Inhaltsaenderung ab, sobald der
Block `running` oder `done` war — `notes` zaehlte zum gesperrten Inhalt. Damit
gab es kein Zeitfenster, in dem sich das Ergebnis eintragen liess: vor dem
Start gibt es noch keins, ab `running` und bei `done` griff die Sperre.
`{"status":"done","notes":"..."}` schlug mit 409 fehl; nur `{"status":"done"}`
allein ging durch.

Titel/Zeit/Dauer bleiben gesperrt (sie beschreiben die ABSICHT, unter der
gearbeitet wurde) — nur `notes` ist jetzt ausgenommen, und wird bei
`running`/`done` ANGEHAENGT statt ersetzt: die urspruengliche Absicht bleibt
stehen, das Ergebnis kommt darunter.
"""

import unittest
from datetime import date

from app.api import day_plan as api
from app.dependencies import AgentPrincipal
from app.models.agent_plan_item import AgentPlanItem
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ADoneBlocksResultCanBeRecordedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(AgentPlanItem.metadata.create_all, tables=[AgentPlanItem.__table__])
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(AgentPlanItem(id=1, agent_id="a1", plan_date=date(2026, 9, 17),
                                  title="Review PR #737", notes="beansprucht: dringend",
                                  status="running"))
            db.add(AgentPlanItem(id=2, agent_id="a1", plan_date=date(2026, 9, 17),
                                  title="Ohne Notiz", notes="", status="running"))
            db.add(AgentPlanItem(id=3, agent_id="a1", plan_date=date(2026, 9, 17),
                                  title="Fertig", notes="Ausgangslage", status="done"))
            db.add(AgentPlanItem(id=4, agent_id="a1", plan_date=date(2026, 9, 17),
                                  title="Noch nicht gestartet", notes="Vorhaben", status="planned"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _agent(agent_id="a1"):
        return AgentPrincipal(id=agent_id, username=f"agent-{agent_id}")

    async def _row(self, item_id):
        async with self.Session() as db:
            return await db.get(AgentPlanItem, item_id)

    async def test_status_done_mit_notes_wird_nicht_mehr_mit_409_abgelehnt(self):
        """Das war der gemeldete Fehler: genau diese Kombination scheiterte."""
        async with self.Session() as db:
            antwort = await api.patch_plan_item(
                1, api.PlanItemPatch(status="done", notes="Review abgeschlossen, 2 Kommentare"),
                user=self._agent(), db=db,
            )
        self.assertEqual(antwort["status"], "done")

    async def test_das_ergebnis_wird_unter_die_urspruengliche_absicht_angehaengt(self):
        async with self.Session() as db:
            await api.patch_plan_item(
                1, api.PlanItemPatch(notes="Ergebnis: erledigt"), user=self._agent(), db=db,
            )
        row = await self._row(1)
        self.assertIn("beansprucht: dringend", row.notes)
        self.assertIn("Ergebnis: erledigt", row.notes)
        # Die Absicht muss VOR dem Ergebnis stehen.
        self.assertLess(row.notes.index("beansprucht"), row.notes.index("Ergebnis"))

    async def test_ohne_vorherige_notiz_wird_einfach_gesetzt_nicht_mit_leerzeile_vorangestellt(self):
        async with self.Session() as db:
            await api.patch_plan_item(
                2, api.PlanItemPatch(notes="Erstes Ergebnis"), user=self._agent(), db=db,
            )
        row = await self._row(2)
        self.assertEqual(row.notes, "Erstes Ergebnis")

    async def test_auch_bei_status_done_laesst_sich_noch_anhaengen(self):
        async with self.Session() as db:
            await api.patch_plan_item(
                3, api.PlanItemPatch(notes="Nachtrag"), user=self._agent(), db=db,
            )
        row = await self._row(3)
        self.assertIn("Ausgangslage", row.notes)
        self.assertIn("Nachtrag", row.notes)

    async def test_notes_allein_loest_bei_running_keine_409_mehr_aus(self):
        async with self.Session() as db:
            antwort = await api.patch_plan_item(
                1, api.PlanItemPatch(notes="Zwischenstand"), user=self._agent(), db=db,
            )
        self.assertIsNotNone(antwort)

    async def test_titel_bleibt_bei_running_weiterhin_gesperrt(self):
        """Die Sperre selbst darf nicht verschwinden — nur notes ist ausgenommen."""
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.patch_plan_item(
                    1, api.PlanItemPatch(title="Anderer Titel"), user=self._agent(), db=db,
                )
        self.assertEqual(fall.exception.status_code, 409)

    async def test_titel_und_notes_zusammen_bleiben_bei_running_gesperrt(self):
        """title ist gesperrt -> die ganze Anfrage schlaegt fehl, notes wird nicht
        heimlich uebernommen, waehrend title abgelehnt wird."""
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as fall:
                await api.patch_plan_item(
                    1, api.PlanItemPatch(title="Anderer Titel", notes="Ergebnis"),
                    user=self._agent(), db=db,
                )
        self.assertEqual(fall.exception.status_code, 409)
        row = await self._row(1)
        self.assertEqual(row.notes, "beansprucht: dringend")

    async def test_vor_dem_start_wird_notes_weiterhin_normal_ersetzt(self):
        """planned: keine Ergebnis-Semantik noetig, ein normales Edit reicht."""
        async with self.Session() as db:
            await api.patch_plan_item(
                4, api.PlanItemPatch(notes="Neues Vorhaben"), user=self._agent(), db=db,
            )
        row = await self._row(4)
        self.assertEqual(row.notes, "Neues Vorhaben")


if __name__ == "__main__":
    unittest.main()
