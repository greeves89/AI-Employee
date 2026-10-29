"""Der Abgleich von `update_todos` muss dort suchen, wo er auch schreibt (Issue #1128).

Gemessen am 29.10.2026: ein Aufruf mit vier Eintraegen machte aus fuenf offenen
Todos neun — Rueckmeldung `0 updated, 4 added`, obwohl drei der vier Titel
woertlich schon vorhanden waren. Zwei Alt-Dubletten im selben Bestand sind auf
demselben Weg entstanden.

Die Ursache lag nicht im Abgleich nach Titel, sondern in der MENGE, ueber die er
lief: der Umfang kam allein aus den Feldern auf Rumpf-Ebene (`body.project`),
obwohl das Schema das Projekt ausdruecklich auch JE EINTRAG erlaubt — und bei
einer Liste ueber mehrere Projekte ist das der einzige Weg. Wer es so angab,
landete im Zweig "Eintraege OHNE Projekt". Ein Eintrag, der ein Projekt HAT,
kann dort nie gefunden werden: jeder Titel galt als neu.

Die Tests laufen gegen eine echte SQLite-Datenbank. Eine Attrappe haette hier
nichts bewiesen — der Fehler steckte in der Abfrage selbst, und eine Attrappe,
die den falschen Wert gar nicht liefern kann, belegt nur sich selbst.
"""

import unittest

from app.api.todos import bulk_update_todos
from app.models.agent_todo import AgentTodo, TodoStatus
from app.schemas.todo import TodoBulkItem, TodoBulkUpdate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

AGENT = "agent-1"


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                AgentTodo.metadata.create_all, tables=[AgentTodo.__table__]
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _anlegen(self, db, titel, *, projekt=None, task_id=None, beschreibung="alt"):
        db.add(
            AgentTodo(
                agent_id=AGENT,
                task_id=task_id,
                project=projekt,
                title=titel,
                description=beschreibung,
                status=TodoStatus.PENDING,
                priority=3,
                sort_order=0,
            )
        )
        await db.commit()

    async def _aufrufen(self, db, todos, *, projekt=None, task_id=None):
        return await bulk_update_todos(
            body=TodoBulkUpdate(task_id=task_id, project=projekt, todos=todos),
            auth={"agent_id": AGENT},
            db=db,
        )

    async def _alle(self, db):
        rows = (
            await db.execute(select(AgentTodo).where(AgentTodo.agent_id == AGENT))
        ).scalars().all()
        return list(rows)


class ProjektNurJeEintrag(_MitDatenbank):
    """Der gemessene Fehlerfall: Projekt steht NUR am Eintrag, nicht am Rumpf."""

    async def test_vorhandener_titel_wird_aktualisiert_statt_verdoppelt(self):
        async with self.Session() as db:
            await self._anlegen(db, "Platte aufraeumen", projekt="AI-Employee")

            antwort = await self._aufrufen(
                db,
                [
                    TodoBulkItem(
                        title="Platte aufraeumen",
                        project="AI-Employee",
                        description="neu",
                    )
                ],
            )

            self.assertEqual(antwort["updated"], 1, "Titel war vorhanden")
            self.assertEqual(antwort["added"], 0, "nichts darf dazukommen")
            alle = await self._alle(db)
            self.assertEqual(len(alle), 1, f"Dublette entstanden: {[t.title for t in alle]}")
            self.assertEqual(alle[0].description, "neu", "der Eintrag wurde nicht fortgeschrieben")

    async def test_zwei_projekte_in_einem_aufruf_werden_beide_aktualisiert(self):
        async with self.Session() as db:
            await self._anlegen(db, "Karte pruefen", projekt="AI-Employee")
            await self._anlegen(db, "Bericht schreiben", projekt="Clearlight")

            antwort = await self._aufrufen(
                db,
                [
                    TodoBulkItem(title="Karte pruefen", project="AI-Employee", description="neu-a"),
                    TodoBulkItem(title="Bericht schreiben", project="Clearlight", description="neu-b"),
                ],
            )

            self.assertEqual(antwort["updated"], 2)
            self.assertEqual(antwort["added"], 0)
            self.assertEqual(len(await self._alle(db)), 2)


class GleicherTitelInZweiProjekten(_MitDatenbank):
    """Sobald der Umfang mehrere Projekte umfasst, ist der Titel allein kein
    eindeutiger Schluessel mehr. Ohne das Projekt im Schluessel wuerde ein
    Eintrag von Projekt A still das Todo von Projekt B ueberschreiben."""

    async def test_gleicher_titel_trifft_nur_das_eigene_projekt(self):
        async with self.Session() as db:
            await self._anlegen(db, "Abnahme", projekt="AI-Employee", beschreibung="a")
            await self._anlegen(db, "Abnahme", projekt="Clearlight", beschreibung="b")

            antwort = await self._aufrufen(
                db, [TodoBulkItem(title="Abnahme", project="Clearlight", description="b-neu")]
            )

            self.assertEqual(antwort["updated"], 1)
            self.assertEqual(antwort["added"], 0)
            nach = {(t.project, t.description) for t in await self._alle(db)}
            self.assertIn(("Clearlight", "b-neu"), nach, "das eigene Projekt wurde nicht fortgeschrieben")
            self.assertIn(("AI-Employee", "a"), nach, "das fremde Projekt wurde mit ueberschrieben")

    async def test_neuer_titel_im_zweiten_projekt_kommt_dazu(self):
        async with self.Session() as db:
            await self._anlegen(db, "Abnahme", projekt="AI-Employee")

            antwort = await self._aufrufen(
                db, [TodoBulkItem(title="Abnahme", project="Clearlight")]
            )

            self.assertEqual(antwort["added"], 1, "anderes Projekt, derselbe Titel = eigener Eintrag")
            self.assertEqual(len(await self._alle(db)), 2)


class DubletteInnerhalbEinesAufrufs(_MitDatenbank):
    async def test_derselbe_titel_zweimal_in_einer_liste_ergibt_einen_eintrag(self):
        async with self.Session() as db:
            antwort = await self._aufrufen(
                db,
                [
                    TodoBulkItem(title="Doppelt", project="AI-Employee", description="erst"),
                    TodoBulkItem(title="Doppelt", project="AI-Employee", description="dann"),
                ],
            )

            self.assertEqual(antwort["added"], 1)
            self.assertEqual(antwort["updated"], 1)
            alle = await self._alle(db)
            self.assertEqual(len(alle), 1, f"Dublette innerhalb eines Aufrufs: {len(alle)}")
            self.assertEqual(alle[0].description, "dann")


class BisherigesVerhaltenBleibt(_MitDatenbank):
    """Gegenprobe: die drei schon vorher funktionierenden Wege duerfen sich
    nicht veraendern — sonst heilt der Fix eine Stelle und bricht drei."""

    async def test_projekt_am_rumpf_trifft_weiterhin(self):
        async with self.Session() as db:
            await self._anlegen(db, "Alt", projekt="AI-Employee")
            antwort = await self._aufrufen(
                db, [TodoBulkItem(title="Alt")], projekt="AI-Employee"
            )
            self.assertEqual((antwort["updated"], antwort["added"]), (1, 0))

    async def test_task_id_umfang_trifft_weiterhin_ueber_den_titel(self):
        async with self.Session() as db:
            await self._anlegen(db, "Schritt 1", task_id="t-1")
            antwort = await self._aufrufen(
                db, [TodoBulkItem(title="Schritt 1", project="AI-Employee")], task_id="t-1"
            )
            self.assertEqual(
                (antwort["updated"], antwort["added"]),
                (1, 0),
                "im task_id-Umfang darf das Projekt den Treffer nicht verhindern",
            )

    async def test_ganz_ohne_projekt_bleibt_beim_alten_umfang(self):
        async with self.Session() as db:
            await self._anlegen(db, "Lose Notiz")
            await self._anlegen(db, "Lose Notiz", projekt="AI-Employee")
            antwort = await self._aufrufen(db, [TodoBulkItem(title="Lose Notiz")])
            self.assertEqual(
                (antwort["updated"], antwort["added"]),
                (1, 0),
                "ohne Projekt darf nur der Eintrag ohne Projekt getroffen werden",
            )
            nach = {(t.project, t.description) for t in await self._alle(db)}
            self.assertIn(("AI-Employee", "alt"), nach, "der Projekt-Eintrag wurde mitgenommen")


if __name__ == "__main__":
    unittest.main()
