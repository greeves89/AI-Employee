"""Mitgelieferte Vorlagen muss auch ein normaler Anwender sehen.

Nutzerbericht vom 2026-08-16, mit Bildschirmfoto: beim Anlegen eines Agenten
stand „Noch keine Vorlagen angelegt" — waehrend in der Datenbank **31**
mitgelieferte Vorlagen lagen, alle auf ``is_published = false``:

    SELECT is_builtin, is_published, count(*) FROM agent_templates GROUP BY 1,2;
    t | f | 31

Ursache: der Seeder legt sie mit ``AgentTemplate(is_builtin=True, **daten)`` an
und setzte ``is_published`` nie — es griff die Vorgabe des Modells (``False``).
Die Liste blendet fuer Nicht-Administratoren alles Unveroeffentlichte aus. Ein
Administrator sah also alle 31 und hielt die Sache fuer in Ordnung; jeder andere
sah null. Damit war die gesamte Vorlagen-Auswahl fuer normale Anwender tot.

Der Entwurf-/Veroeffentlichen-Ablauf ist fuer die vom Administrator selbst
geschriebenen Vorlagen gedacht (``create_template`` setzt dort bewusst
``is_published=False``). Was mit dem Produkt kommt, ist fertig.

Der Nachzug fuer bestehende Anlagen laeuft hier gegen eine echte Datenbank —
Quelltext zu durchsuchen haette den Fehler nicht gefunden, denn der Zweig, der
fehlte, war gar keiner: es fehlte ein nicht gesetztes Feld.
"""

import unittest
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.agent_templates import publish_builtin_templates_once
from app.models.agent_template import AgentTemplate
from app.models.platform_settings import PlatformSettings

ROOT = Path(__file__).resolve().parents[2]
MAIN = (ROOT / "orchestrator/app/main.py").read_text()
API = (ROOT / "orchestrator/app/api/templates.py").read_text()


def _vorlage(name: str, *, builtin: bool, published: bool) -> AgentTemplate:
    return AgentTemplate(
        name=name, display_name=name, description="", role="",
        is_builtin=builtin, is_published=published,
    )


class TheBackfillRunsAgainstARealDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(
                AgentTemplate.metadata.create_all,
                tables=[AgentTemplate.__table__, PlatformSettings.__table__],
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.Session()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def _zustand(self, name: str) -> bool:
        from sqlalchemy import select
        return await self.db.scalar(
            select(AgentTemplate.is_published).where(AgentTemplate.name == name)
        )

    async def test_the_reported_situation_is_repaired(self):
        """31 unsichtbare Vorlagen — nachher sichtbar."""
        for i in range(31):
            self.db.add(_vorlage(f"builtin-{i}", builtin=True, published=False))
        await self.db.commit()

        self.assertEqual(await publish_builtin_templates_once(self.db), 31)
        self.assertTrue(await self._zustand("builtin-0"))

    async def test_an_admins_own_draft_is_left_alone(self):
        """Ein Entwurf darf nicht ungefragt fuer alle sichtbar werden."""
        self.db.add(_vorlage("builtin-1", builtin=True, published=False))
        self.db.add(_vorlage("entwurf", builtin=False, published=False))
        await self.db.commit()

        self.assertEqual(await publish_builtin_templates_once(self.db), 1)
        self.assertFalse(await self._zustand("entwurf"))

    async def test_it_runs_only_once(self):
        """Der zweite Start darf nichts mehr tun — sonst kaeme eine abgewaehlte
        Vorlage bei jedem Neustart zurueck."""
        self.db.add(_vorlage("builtin-1", builtin=True, published=False))
        await self.db.commit()
        self.assertEqual(await publish_builtin_templates_once(self.db), 1)

        self.assertEqual(await publish_builtin_templates_once(self.db), 0)

    async def test_a_deliberate_unpublish_survives_a_restart(self):
        """Genau der Grund fuer die Marke, im Ablauf nachgestellt: Nachzug,
        Administrator waehlt ab, Neustart."""
        self.db.add(_vorlage("builtin-1", builtin=True, published=False))
        await self.db.commit()
        await publish_builtin_templates_once(self.db)

        from sqlalchemy import update
        await self.db.execute(
            update(AgentTemplate)
            .where(AgentTemplate.name == "builtin-1")
            .values(is_published=False, published_at=None)
        )
        await self.db.commit()

        await publish_builtin_templates_once(self.db)   # Neustart
        self.assertFalse(await self._zustand("builtin-1"))

    async def test_nothing_to_do_is_recorded_too(self):
        """Sonst laeuft die Abfrage bei jedem Start erneut ueber die Tabelle."""
        self.assertEqual(await publish_builtin_templates_once(self.db), 0)
        from sqlalchemy import select
        marke = await self.db.scalar(select(PlatformSettings))
        self.assertIsNotNone(marke)

    async def test_the_marker_and_the_change_share_one_commit(self):
        """Bricht der Start dazwischen ab, waere sonst das eine ohne das andere
        geschrieben."""
        self.db.add(_vorlage("builtin-1", builtin=True, published=False))
        await self.db.commit()
        await publish_builtin_templates_once(self.db)

        from sqlalchemy import select
        async with self.Session() as frisch:
            self.assertTrue(await frisch.scalar(
                select(AgentTemplate.is_published).where(AgentTemplate.name == "builtin-1")))
            self.assertIsNotNone(await frisch.scalar(select(PlatformSettings)))


class NewInstallationsShipThemVisibleTests(unittest.TestCase):
    """Der Nachzug allein reicht nicht — eine frisch aufgesetzte Anlage laeuft
    ihn mit null Zeilen und haette danach wieder unsichtbare Vorlagen."""

    SEEDER = MAIN.split("for tmpl_data in BUILTIN_TEMPLATES:", 1)[1].split("Seeded/synced", 1)[0]

    def test_a_freshly_seeded_builtin_is_published(self):
        anlegen = self.SEEDER.split("if not existing:", 1)[1].split("elif existing.is_builtin:", 1)[0]
        self.assertIn("is_published=True", anlegen)

    def test_it_carries_a_publication_date(self):
        anlegen = self.SEEDER.split("if not existing:", 1)[1].split("elif existing.is_builtin:", 1)[0]
        self.assertIn("published_at=", anlegen)

    def test_the_sync_branch_does_not_republish(self):
        """Der Abgleich bestehender Vorlagen darf das Abwaehlen eines
        Administrators nicht bei jedem Start ueberschreiben."""
        abgleich = self.SEEDER.split("elif existing.is_builtin:", 1)[1]
        self.assertNotIn("is_published", abgleich)


class TheVisibilityRuleItselfStaysTests(unittest.TestCase):
    """Die Regel ist richtig — sie traf nur auf durchweg unveroeffentlichte
    Vorlagen. Sie darf nicht als „Korrektur" aufgeweicht werden."""

    def test_non_admins_still_see_only_published_templates(self):
        self.assertIn("if user.role not in (UserRole.ADMIN, UserRole.MANAGER):", API)
        self.assertIn("query.where(AgentTemplate.is_published == True)", API)

    def test_starting_from_an_unpublished_template_is_still_refused(self):
        self.assertIn("This template is not published yet", API)

    def test_an_admin_authored_template_still_starts_as_a_draft(self):
        self.assertIn("is_published=False", API)


if __name__ == "__main__":
    unittest.main()
