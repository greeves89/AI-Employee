"""Vorlagen-Auswahl für Fachanwender (#903).

Im Markttest stand die Entwicklung oben, die Buchhaltung irgendwo weiter unten,
und auf den Kacheln Kürzel wie „package-install“ oder „full-access“. Der Server
legt deshalb die Reihenfolge fest (Fachbereiche zuerst, Technik danach) — damit
Web und iOS dieselbe Liste zeigen — und liefert zu den Rechte-Paketen deutsche
Namen mit.
"""

import unittest
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.templates import get_template, list_templates
from app.core.agent_templates import BUILTIN_TEMPLATES, KATEGORIE_REIHENFOLGE, vorlagen_sortierschluessel
from app.models.agent_template import AgentTemplate
from app.models.skill import Skill
from app.models.user import UserRole

FACH = {"finance", "general", "sales", "marketing", "support", "management", "writing", "productivity"}
TECHNIK = {"data", "dev", "ops", "security"}


def _vorlage(name, anzeige, kategorie, *, rechte=(), veroeffentlicht=True, mitgeliefert=True):
    return AgentTemplate(
        name=name, display_name=anzeige, description="", icon="", category=kategorie, model="",
        role="", permissions=list(rechte), integrations=[], mcp_server_ids=[], skill_ids=[],
        knowledge_template="", is_builtin=mitgeliefert, is_published=veroeffentlicht,
    )


class ServerSortiertFachvorlagenZuerst(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda c: Skill.__table__.create(c))
            await conn.run_sync(lambda c: AgentTemplate.__table__.create(c))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            # Absichtlich in „falscher“ Reihenfolge angelegt; die eigene Vorlage
            # des Administrators (nicht mitgeliefert) stand früher immer hinten.
            db.add_all([
                _vorlage("fullstack-developer", "Fullstack-Entwicklung", "dev", rechte=["package-install", "system-config"]),
                _vorlage("devops-engineer", "Server & Betrieb (DevOps)", "ops", rechte=["package-install", "full-access"]),
                _vorlage("security-auditor", "Sicherheitsprüfung", "security"),
                _vorlage("data-analyst", "Datenanalyse", "data"),
                _vorlage("dispatcher", "Disposition", "general"),
                _vorlage("quote-clerk", "Angebot & Kalkulation", "general"),
                _vorlage("uebersetzung", "Übersetzung", "general"),
                _vorlage("sales-agent", "Vertrieb", "sales"),
                _vorlage("payroll-clerk", "Lohnbuchhaltung", "finance"),
                _vorlage("eigene-buchhaltung", "Buchhaltung", "finance", mitgeliefert=False),
                _vorlage("entwurf", "Entwurf", "finance", veroeffentlicht=False, mitgeliefert=False),
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _liste(self, rolle):
        async with self.Session() as db:
            out = await list_templates(user=SimpleNamespace(id="u1", role=rolle), db=db)
        return out["templates"]

    async def test_mitglied_sieht_fachbereiche_vor_der_technik(self):
        namen = [t["display_name"] for t in await self._liste(UserRole.MEMBER)]
        self.assertEqual(namen, [
            "Buchhaltung", "Lohnbuchhaltung",
            "Angebot & Kalkulation", "Disposition", "Übersetzung",
            "Vertrieb",
            "Datenanalyse", "Fullstack-Entwicklung", "Server & Betrieb (DevOps)", "Sicherheitsprüfung",
        ])

    async def test_administrator_bekommt_dieselbe_reihenfolge_samt_entwuerfen(self):
        namen = [t["display_name"] for t in await self._liste(UserRole.ADMIN)]
        self.assertEqual(namen[:3], ["Buchhaltung", "Entwurf", "Lohnbuchhaltung"])
        self.assertEqual(namen[-1], "Sicherheitsprüfung")

    async def test_rechte_pakete_kommen_mit_deutschem_namen(self):
        vorlagen = {t["name"]: t for t in await self._liste(UserRole.ADMIN)}
        self.assertEqual(vorlagen["fullstack-developer"]["permissions_anzeige"],
                         ["Paketinstallation", "Systemkonfiguration"])
        self.assertEqual(vorlagen["devops-engineer"]["permissions_anzeige"],
                         ["Paketinstallation", "Voller Root-Zugriff"])
        self.assertEqual(vorlagen["sales-agent"]["permissions_anzeige"], [])
        # Die Schlüssel bleiben für die Anlage-Logik erhalten.
        self.assertEqual(vorlagen["devops-engineer"]["permissions"], ["package-install", "full-access"])

    async def test_einzelne_vorlage_nennt_die_rechte_ebenso_deutsch(self):
        vorlagen = {t["name"]: t for t in await self._liste(UserRole.ADMIN)}
        async with self.Session() as db:
            out = await get_template(template_id=vorlagen["devops-engineer"]["id"],
                                     user=SimpleNamespace(id="u1", role=UserRole.ADMIN), db=db)
        self.assertEqual(out["permissions_anzeige"], ["Paketinstallation", "Voller Root-Zugriff"])


class MitgelieferteVorlagenFolgenDerRangliste(unittest.TestCase):
    def test_jede_benutzte_kategorie_steht_in_der_rangliste(self):
        for kategorie in {t["category"] for t in BUILTIN_TEMPLATES}:
            with self.subTest(kategorie=kategorie):
                self.assertIn(kategorie, KATEGORIE_REIHENFOLGE)

    def test_alle_fachvorlagen_stehen_vor_jeder_technikvorlage(self):
        sortiert = sorted(BUILTIN_TEMPLATES, key=lambda t: vorlagen_sortierschluessel(t["category"], t["display_name"]))
        letzte_fach = max(i for i, t in enumerate(sortiert) if t["category"] in FACH)
        erste_technik = min(i for i, t in enumerate(sortiert) if t["category"] in TECHNIK)
        self.assertLess(letzte_fach, erste_technik)
        self.assertEqual(sortiert[0]["name"], "bookkeeper")

    def test_unbekannte_kategorie_landet_hinten_statt_zu_verschwinden(self):
        self.assertGreater(vorlagen_sortierschluessel("gibt-es-nicht", "A"),
                           vorlagen_sortierschluessel("security", "Z"))


class SkillsKommenMitLesbaremTitel(unittest.IsolatedAsyncioTestCase):
    """Auf der Kachel steht „Belege vorkontieren“, nicht ``buchhaltung-vorkontieren``."""

    async def asyncSetUp(self):
        from app.models.skill import SkillCategory, SkillStatus

        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda c: Skill.__table__.create(c))
            await conn.run_sync(lambda c: AgentTemplate.__table__.create(c))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            for i, (name, inhalt) in enumerate([
                ("buchhaltung-vorkontieren", "# Belege vorkontieren\n\nDu bereitest Belege auf."),
                ("xlsx", "# XLSX creation\n\n..."),
                ("ohne-ueberschrift", "Nur Text, keine Überschrift."),
                ("mit-kopf", "---\nname: mit-kopf\n---\n\n# Vertrag prüfen\n"),
            ], 1):
                db.add(Skill(id=i, name=name, description="", content=inhalt, category=SkillCategory.TEMPLATE,
                             status=SkillStatus.ACTIVE, created_by="test"))
            v = _vorlage("bookkeeper", "Buchhaltung", "finance")
            v.skill_ids = [1, 2, 3, 4]
            db.add(v)
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_titel_aus_ueberschrift_formatname_oder_name(self):
        async with self.Session() as db:
            out = await list_templates(user=SimpleNamespace(id="u1", role=UserRole.MEMBER), db=db)
        titel = {s["name"]: s["titel"] for s in out["templates"][0]["skills"]}
        self.assertEqual(titel, {
            "buchhaltung-vorkontieren": "Belege vorkontieren",
            "xlsx": "Excel-Tabellen",
            "ohne-ueberschrift": "ohne-ueberschrift",
            "mit-kopf": "Vertrag prüfen",
        })


class SucheNachRechnungFindetBuchhaltung(unittest.TestCase):
    """Die Suche im Anlege-Dialog läuft über Name, Beschreibung, Rolle und Skills."""

    def test_buchhaltung_nennt_rechnungen(self):
        buchhaltung = next(t for t in BUILTIN_TEMPLATES if t["name"] == "bookkeeper")
        durchsuchbar = " ".join([buchhaltung["display_name"], buchhaltung["description"], buchhaltung["role"]])
        self.assertIn("rechnung", durchsuchbar.lower())


if __name__ == "__main__":
    unittest.main()
