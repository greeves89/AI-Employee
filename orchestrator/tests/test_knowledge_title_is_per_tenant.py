"""Ein Wissenstitel gehoert einem Mandanten, nicht der ganzen Anlage (Issue #655).

`knowledge_entries.title` trug eine GLOBALE Unique-Bedingung aus der Zeit vor der
Mandantentrennung. Die Spalte `user_id` kam erst spaeter dazu, und der Schreibpfad
sucht seitdem konsequent nach `(title, user_id)`. Daraus entstand ein Zustand, den
der Code fuer unmoeglich hielt: die Suche findet nichts (der Titel gehoert einem
anderen Besitzer), also folgt ein INSERT — und der scheitert.

In der Nacht zum 2026-08-24 brach der Reflexionslauf daran ab:

    duplicate key value violates unique constraint "ix_knowledge_entries_title"
    DETAIL:  Key (title)=(Klare Aufgabendefinition) already exists.

Und zwar nicht nur der eine Eintrag: die UniqueViolation nahm die Session mit
(`transaction has been rolled back`), alle weiteren Erkenntnisse desselben Laufs
gingen verloren. Reflexionen erzeugen naturgemaess generische Titel — die
Kollision war also nicht der Ausnahme-, sondern der Regelfall.

Die Tests laufen gegen SQLite, das teilweise Unique-Indizes genauso kennt wie
Postgres. Deshalb traegt das Modell `sqlite_where` UND `postgresql_where`: sonst
haetten die Indizes hier keine Bedingung und der Test bewiese nichts.
"""

import unittest

from app.core.knowledge_write import write_entry
from app.models.knowledge import KnowledgeEntry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

#: Woertlich der Titel, an dem der Lauf zerbrach.
KOLLIDIERENDER_TITEL = "Klare Aufgabendefinition"


class _MitDatenbank(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                KnowledgeEntry.metadata.create_all,
                tables=[KnowledgeEntry.__table__],
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _titel_zaehlen(self, db, titel):
        rows = (await db.execute(
            select(KnowledgeEntry).where(KnowledgeEntry.title == titel)
        )).scalars().all()
        return rows


class ZweiMandantenDuerfenDenselbenTitelFuehrenTests(_MitDatenbank):
    """Der Kern von #655."""

    async def test_beide_gelingen_und_bleiben_getrennte_zeilen(self):
        async with self.Session() as db:
            a, a_neu = await write_entry(
                db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                content="Sicht des ersten Mandanten", author="reflection",
            )
            b, b_neu = await write_entry(
                db, user_id="tenant-b", title=KOLLIDIERENDER_TITEL,
                content="Sicht des zweiten Mandanten", author="reflection",
            )

            self.assertTrue(a_neu)
            self.assertTrue(b_neu)
            self.assertNotEqual(a.id, b.id)

            rows = await self._titel_zaehlen(db, KOLLIDIERENDER_TITEL)
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                {r.user_id for r in rows}, {"tenant-a", "tenant-b"},
            )

    async def test_der_inhalt_des_einen_bleibt_dem_anderen_unbekannt(self):
        """Ohne die Trennung haette der zweite Schreibvorgang den ersten
        ueberschrieben — genau davor warnt der Docstring von ``write_entry``."""
        async with self.Session() as db:
            await write_entry(db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                              content="Sicht des ersten Mandanten", author="reflection")
            await write_entry(db, user_id="tenant-b", title=KOLLIDIERENDER_TITEL,
                              content="Sicht des zweiten Mandanten", author="reflection")

            rows = await self._titel_zaehlen(db, KOLLIDIERENDER_TITEL)
            inhalte = {r.user_id: r.content for r in rows}
            self.assertEqual(inhalte["tenant-a"], "Sicht des ersten Mandanten")
            self.assertEqual(inhalte["tenant-b"], "Sicht des zweiten Mandanten")

    async def test_ein_dritter_mandant_kommt_auch_noch_durch(self):
        async with self.Session() as db:
            for tenant in ("tenant-a", "tenant-b", "tenant-c"):
                await write_entry(db, user_id=tenant, title=KOLLIDIERENDER_TITEL,
                                  content=f"Sicht von {tenant}", author="reflection")
            rows = await self._titel_zaehlen(db, KOLLIDIERENDER_TITEL)
            self.assertEqual(len(rows), 3)


class DerselbeMandantErgaenztStattZuDuplizierenTests(_MitDatenbank):
    """Das bisherige ``write_entry``-Verhalten darf sich nicht mitverschieben."""

    async def test_zweimal_derselbe_titel_ergibt_eine_zeile(self):
        async with self.Session() as db:
            erst, erst_neu = await write_entry(
                db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                content="erste Fassung", author="reflection",
            )
            wieder, wieder_neu = await write_entry(
                db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                content="zweite Fassung", author="reflection",
            )

            self.assertTrue(erst_neu)
            self.assertFalse(wieder_neu)
            self.assertEqual(erst.id, wieder.id)
            self.assertEqual(len(await self._titel_zaehlen(db, KOLLIDIERENDER_TITEL)), 1)

    async def test_der_inhalt_wird_ersetzt_und_die_tags_vereinigt(self):
        async with self.Session() as db:
            await write_entry(db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                              content="erste Fassung", tags=["reflection"],
                              author="reflection")
            eintrag, _ = await write_entry(
                db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                content="zweite Fassung", tags=["meeting"], author="reflection",
            )
            self.assertEqual(eintrag.content, "zweite Fassung")
            self.assertEqual(sorted(eintrag.tags), ["meeting", "reflection"])


class GlobaleEintraegeBleibenGlobalEindeutigTests(_MitDatenbank):
    """Ein einfacher Index auf ``(title, user_id)`` haette hier durchgelassen:
    NULLs gelten darin als verschieden. Genau deshalb zwei teilweise Indizes."""

    async def test_zwei_globale_eintraege_gleichen_titels_bleiben_verboten(self):
        async with self.Session() as db:
            db.add(KnowledgeEntry(title=KOLLIDIERENDER_TITEL, content="global",
                                  tags=[], user_id=None))
            await db.commit()

            db.add(KnowledgeEntry(title=KOLLIDIERENDER_TITEL, content="nochmal global",
                                  tags=[], user_id=None))
            with self.assertRaises(IntegrityError):
                await db.commit()
            await db.rollback()

    async def test_ein_globaler_und_ein_eigener_eintrag_vertragen_sich(self):
        async with self.Session() as db:
            db.add(KnowledgeEntry(title=KOLLIDIERENDER_TITEL, content="global",
                                  tags=[], user_id=None))
            await db.commit()
            eintrag, neu = await write_entry(
                db, user_id="tenant-a", title=KOLLIDIERENDER_TITEL,
                content="eigener", author="reflection",
            )
            self.assertTrue(neu)
            self.assertEqual(len(await self._titel_zaehlen(db, KOLLIDIERENDER_TITEL)), 2)


class DerselbeMandantBekommtWeiterhinKeineDuplikateTests(_MitDatenbank):
    """Die gelockerte Bedingung darf dem EINZELNEN Mandanten nicht erlauben,
    denselben Titel zweimal zu fuehren — sonst waere die Suche in seinem Vault
    ab da mehrdeutig."""

    async def test_ein_direktes_zweites_insert_desselben_mandanten_scheitert(self):
        async with self.Session() as db:
            db.add(KnowledgeEntry(title=KOLLIDIERENDER_TITEL, content="erste",
                                  tags=[], user_id="tenant-a"))
            await db.commit()

            db.add(KnowledgeEntry(title=KOLLIDIERENDER_TITEL, content="zweite",
                                  tags=[], user_id="tenant-a"))
            with self.assertRaises(IntegrityError):
                await db.commit()
            await db.rollback()


class DasModellTraegtDieBedingungFuerBeideDialekteTests(unittest.TestCase):
    """Ohne ``sqlite_where`` waeren die Indizes im Test bedingungslos — die
    Tests oben wuerden dann etwas anderes pruefen als das, was in Produktion
    laeuft, und der globale Fall waere gar nicht abgedeckt."""

    def test_beide_teilweisen_indizes_existieren(self):
        namen = {ix.name for ix in KnowledgeEntry.__table__.indexes}
        self.assertIn("uq_knowledge_entries_title_global", namen)
        self.assertIn("uq_knowledge_entries_title_per_user", namen)

    def test_sie_sind_eindeutig_und_bedingt_fuer_postgres_und_sqlite(self):
        for name in ("uq_knowledge_entries_title_global",
                     "uq_knowledge_entries_title_per_user"):
            ix = next(i for i in KnowledgeEntry.__table__.indexes if i.name == name)
            with self.subTest(index=name):
                self.assertTrue(ix.unique)
                self.assertIsNotNone(ix.dialect_options["postgresql"]["where"])
                self.assertIsNotNone(ix.dialect_options["sqlite"]["where"])

    def test_die_spalte_selbst_traegt_keine_globale_bedingung_mehr(self):
        self.assertFalse(KnowledgeEntry.__table__.c.title.unique)


class EinEintragWenigerStattEinerNachtOhneReflexionTests(unittest.IsolatedAsyncioTestCase):
    """Der zweite Teil von #655: die UniqueViolation nahm die Session mit, und
    damit alle uebrigen Erkenntnisse desselben Laufs."""

    def _dienst(self):
        from app.services.reflection_service import ReflectionService

        return ReflectionService()

    async def test_ein_gescheiterter_eintrag_wird_gezaehlt_statt_geworfen(self):
        from unittest import mock

        svc = self._dienst()
        db = mock.AsyncMock()
        stats = {"kb_entries": 0, "kb_skipped": 0}

        with mock.patch.object(
            svc, "_apply_knowledge", side_effect=IntegrityError("INSERT", {}, Exception("boom"))
        ):
            ok = await svc._apply_knowledge_guarded(db, 1, {"title": "Irgendwas"}, stats)

        self.assertFalse(ok)
        self.assertEqual(stats["kb_skipped"], 1)

    async def test_die_vergiftete_session_wird_zurueckgerollt(self):
        """Ohne ``rollback`` endet jede weitere Anweisung des Laufs mit
        „transaction has been rolled back" — genau der Totalausfall aus #655."""
        from unittest import mock

        svc = self._dienst()
        db = mock.AsyncMock()
        with mock.patch.object(
            svc, "_apply_knowledge", side_effect=IntegrityError("INSERT", {}, Exception("boom"))
        ):
            await svc._apply_knowledge_guarded(db, 1, {"title": "Irgendwas"}, {})
        db.rollback.assert_awaited_once()

    async def test_ein_gelungener_eintrag_meldet_erfolg(self):
        from unittest import mock

        svc = self._dienst()
        db = mock.AsyncMock()
        stats = {"kb_entries": 0, "kb_skipped": 0}
        with mock.patch.object(svc, "_apply_knowledge", return_value=None):
            self.assertTrue(await svc._apply_knowledge_guarded(db, 1, {"title": "X"}, stats))
        self.assertEqual(stats["kb_skipped"], 0)
        db.rollback.assert_not_awaited()


class DerFreigabewegSchlaegtWeiterhinDurchTests(unittest.TestCase):
    """Wer auf „freigeben" geklickt hat, muss den Fehler sehen — ein stilles
    Nichts waere hier schlimmer als eine Fehlermeldung."""

    def test_die_freigabe_ruft_die_ungeschuetzte_fassung(self):
        import inspect

        from app.services.reflection_service import apply_reflection_approval

        src = inspect.getsource(apply_reflection_approval)
        self.assertIn("_apply_knowledge(", src)
        self.assertNotIn("_apply_knowledge_guarded(", src)

    def test_der_naechtliche_lauf_ruft_die_geschuetzte_fassung(self):
        import inspect

        from app.services.reflection_service import ReflectionService

        src = inspect.getsource(ReflectionService)
        self.assertEqual(src.count("await self._apply_knowledge_guarded("), 2)


if __name__ == "__main__":
    unittest.main()
