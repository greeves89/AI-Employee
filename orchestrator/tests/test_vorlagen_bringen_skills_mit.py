"""Mitgelieferte Vorlagen bringen passende Skills mit.

Vorher hatte KEINE der mitgelieferten Vorlagen einen Skill (skill_ids leer), obwohl
der Marktplatz über hundert passende Skills aus öffentlichen Quellen kennt. Skill-IDs
sind je Anlage verschieden (was gecrawlt wurde, in welcher Reihenfolge) — deshalb
benennen die Vorlagen ihre Skills über den NAMEN, aufgelöst auf jeder Anlage.
Für Fachgebiete ohne brauchbare öffentliche Skills (Buchhaltung, Recht, Lohn …)
liefert das Produkt eigene Skills mit (app/skills_mitgeliefert), die auch offline da sind.
"""

import asyncio
import unittest
from pathlib import Path

from app.core import mitgelieferte_skills as ms
from app.core import vorlagen_skills as vs
from app.core.agent_templates import BUILTIN_TEMPLATES

_ORDNER = Path(__file__).resolve().parents[1] / "app" / "skills_mitgeliefert"


class ZuordnungTests(unittest.TestCase):
    def test_jede_mitgelieferte_vorlage_hat_skills(self):
        namen = {t["name"] for t in BUILTIN_TEMPLATES}
        fehlend = sorted(n for n in namen if not vs.VORLAGEN_SKILLS.get(n))
        self.assertEqual(fehlend, [], "Vorlagen ohne Skills")

    def test_zuordnung_nennt_nur_echte_vorlagen(self):
        namen = {t["name"] for t in BUILTIN_TEMPLATES}
        self.assertEqual(sorted(set(vs.VORLAGEN_SKILLS) - namen), [])

    def test_mitgelieferte_skills_aus_der_zuordnung_existieren(self):
        vorhanden = {s["name"] for s in ms.laden()}
        verlangt = {n for liste in vs.VORLAGEN_SKILLS.values() for n in liste}
        # Was nicht mitgeliefert ist, muss aus einer Standardquelle kommen.
        from app.services.skill_crawler import DEFAULT_SKILL_REPOS
        self.assertTrue(DEFAULT_SKILL_REPOS)
        for name in verlangt - vorhanden:
            self.assertIn(name, vs.OEFFENTLICHE_SKILLS, f"{name}: weder mitgeliefert noch als öffentlich bekannt")


class MitgelieferteSkillsTests(unittest.TestCase):
    def test_jeder_ordner_hat_ein_gueltiges_skill_md(self):
        skills = ms.laden()
        self.assertGreaterEqual(len(skills), 10)
        for s in skills:
            with self.subTest(s["name"]):
                self.assertRegex(s["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
                # Bis 700: die Beschreibung trägt seit der Abnahme von v1.362.1 auch die
                # Auslöser („Auslöser: DATEV, Buchungsstapel …“) — sie sind das, woran
                # Modell und Server erkennen, WANN der Skill gilt. Claude erlaubt 1024.
                self.assertTrue(20 <= len(s["description"]) <= 700, s["description"])
                self.assertGreater(len(s["content"].splitlines()), 30)
                self.assertEqual(s["source_repo"], ms.QUELLE)

    def test_ordnername_gleich_skillname(self):
        for s in ms.laden():
            self.assertTrue((_ORDNER / s["name"] / "SKILL.md").exists(), s["name"])

    def test_bestehen_die_sicherheitspruefung(self):
        from app.core.skill_security import check_skill_content
        for s in ms.laden():
            check_skill_content(s["content"])  # wirft bei Befund

    def test_keine_umlaut_ersatzschreibung_in_texten(self):
        import re
        for s in ms.laden():
            # Code-Spannen (`vertrag-pruefen`, Werkzeugnamen) sind Bezeichner, kein Fließtext.
            text = re.sub(r"`[^`]*`", "", s["description"] + "\n" + s["content"])
            treffer = re.findall(r"\b\w*(?:fuer|ueber|koenn|muess|waehl|groess|aender|naech|pruef)\w*\b", text, re.I)
            self.assertEqual(treffer, [], s["name"])


class _Ergebnis:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    """Nur die eine Abfrage: (id, name, source_repo) aktiver Skills mit Namen aus der Liste."""

    def __init__(self, skills, herkunft="anthropics/skills"):
        self._skills = skills  # name -> id oder name -> (id, herkunft)
        self._herkunft = herkunft

    async def execute(self, _stmt):
        zeilen = []
        for n, v in self._skills.items():
            sid, herkunft, erstellt = (v + ("import:github",))[:3] if isinstance(v, tuple) else (v, self._herkunft, "import:github")
            if herkunft == ms.QUELLE and not (isinstance(v, tuple) and len(v) > 2):
                erstellt = "import:mitgeliefert"
            zeilen.append((sid, n, herkunft, erstellt))
        return _Ergebnis(zeilen)


class AufloesungTests(unittest.TestCase):
    def test_namen_werden_in_reihenfolge_zu_ids(self):
        db = _Db({"docx": 7, "pdf": 3, "xlsx": 9})
        ids = asyncio.run(vs.ids_fuer_namen(db, ["pdf", "fehlt", "docx"]))
        self.assertEqual(ids, [3, 7])

    def test_gleichnamiger_selbst_angelegter_skill_kapert_keine_vorlage(self):
        # Ein Nutzer legt vorher "vertrag-pruefen" selbst an (source_repo leer) —
        # die Vorlage darf ihn NICHT bekommen, nur den mitgelieferten bzw. öffentlichen.
        db = _Db({"vertrag-pruefen": (5, None), "docx": (7, "anthropics/skills"),
                  "buchhaltung-vorkontieren": (8, ms.QUELLE), "fremd": (9, "irgendwer/skills")})
        ids = asyncio.run(vs.ids_fuer_namen(db, ["vertrag-pruefen", "docx", "buchhaltung-vorkontieren", "fremd"]))
        self.assertEqual(ids, [7, 8])

    def test_admin_quelle_mit_gleichem_ort_zaehlt_nicht(self):
        # Eine vom Admin eingetragene Quelle "getsentry/skills" (eigener Branch) hat
        # created_by "import:source:<id>" — ihre Skills dürfen keine Vorlage füllen.
        db = _Db({"find-bugs": (4, "getsentry/skills", "import:source:7"),
                  "docx": (7, "anthropics/skills", "import:github")})
        self.assertEqual(asyncio.run(vs.ids_fuer_namen(db, ["find-bugs", "docx"])), [7])

    def test_ohne_namen_keine_abfrage(self):
        class _Leer:
            async def execute(self, _s):
                raise AssertionError("keine Abfrage ohne Namen")
        self.assertEqual(asyncio.run(vs.ids_fuer_namen(_Leer(), [])), [])


if __name__ == "__main__":
    unittest.main()
