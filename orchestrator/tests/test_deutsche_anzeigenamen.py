"""Jede Rolle, jede Vorlagen-Kategorie und jede mitgelieferte Vorlage hat einen
deutschen Anzeigenamen (#902).

Im Markttest stand in der Nutzerverwaltung „Member“ und „Viewer“, in der
Vorlagenauswahl „Development“, „Data Analyst“ und „Security Auditor“ — in einer
deutschen Oberfläche. Ursache war jedes Mal dasselbe Muster: zwei Listen, die
niemand gegeneinander gehalten hat (Server-Werte gegen Anzeige-Namen im
Frontend). Diese Tests halten sie zusammen:

* jede ``UserRole`` steht mit Namen in ``frontend/src/lib/rollen.ts``,
* jede benutzte Vorlagen-Kategorie steht mit deutschem Namen in
  ``frontend/src/lib/vorlagen-kategorien.ts``,
* kein mitgelieferter Vorlagen-Anzeigename ist englisch (``name`` bleibt der
  stabile Schlüssel und darf englisch sein).

Und die Hauptabläufe Vorlagen / Zeitpläne / Agenten melden ihre Fehler deutsch:
das ist, was im Dialog beim Anwender ankommt.
"""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from app.core.agent_templates import BUILTIN_TEMPLATES
from app.models.user import UserRole

ROOT = Path(__file__).resolve().parents[2]
ROLLEN_TS = (ROOT / "frontend/src/lib/rollen.ts").read_text()
KATEGORIEN_TS = (ROOT / "frontend/src/lib/vorlagen-kategorien.ts").read_text()


def _ts_map(quelle: str, name: str) -> dict[str, str]:
    """Schlüssel/Wert-Paare eines ``const NAME … = { a: "b", … }`` aus TypeScript."""
    rumpf = quelle.split(f"export const {name}", 1)[1].split("{", 1)[1].split("};", 1)[0]
    return dict(re.findall(r'^\s*(\w+):\s*"([^"]*)"', rumpf, re.M))


#: Englische Wörter, die in einem deutschen Anzeigenamen nichts verloren haben.
#: Bewusst nur eindeutige Fälle — „Marketing“, „Social Media“, „API“ oder
#: „DevOps“ sind im Deutschen gebräuchlich.
ENGLISCH = re.compile(
    r"\b(developer|analyst|writer|engineer|assistant|designer|specialist|"
    r"manager|auditor|reviewer|tester|translator|recruiter|crawler|admin|"
    r"agent|development|analytics|operations|creative|general|sales|security|"
    r"management|support|member|viewer|unassigned)\b",
    re.IGNORECASE,
)


class RollenHabenNamen(unittest.TestCase):
    NAMEN = _ts_map(ROLLEN_TS, "ROLLEN_NAMEN")

    def test_jede_rolle_hat_einen_deutschen_namen(self):
        for rolle in UserRole:
            with self.subTest(rolle=rolle.value):
                self.assertIn(rolle.value, self.NAMEN)
                name = self.NAMEN[rolle.value]
                self.assertTrue(name.strip())
                self.assertEqual(ENGLISCH.findall(name), [], name)

    def test_keine_rolle_zu_viel(self):
        """Eine Rolle, die es im Server nicht gibt, wäre ein Tippfehler."""
        self.assertEqual(set(self.NAMEN), {r.value for r in UserRole})


class VorlagenSindDeutsch(unittest.TestCase):
    KATEGORIEN = _ts_map(KATEGORIEN_TS, "VORLAGEN_KATEGORIEN")
    FARBEN = _ts_map(KATEGORIEN_TS, "VORLAGEN_KATEGORIE_FARBEN")

    def test_jede_kategorie_hat_einen_deutschen_namen_und_eine_farbe(self):
        for kategorie in {t["category"] for t in BUILTIN_TEMPLATES}:
            with self.subTest(kategorie=kategorie):
                self.assertIn(kategorie, self.KATEGORIEN)
                self.assertIn(kategorie, self.FARBEN)
                self.assertEqual(ENGLISCH.findall(self.KATEGORIEN[kategorie]), [])

    def test_kein_anzeigename_ist_englisch(self):
        for t in BUILTIN_TEMPLATES:
            with self.subTest(vorlage=t["name"]):
                self.assertEqual(ENGLISCH.findall(t["display_name"]), [], t["display_name"])


def _db_ohne_treffer():
    db = MagicMock()
    ergebnis = MagicMock()
    ergebnis.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=ergebnis)
    db.scalar = AsyncMock(return_value=None)
    return db


class FehlerDerHauptablaeufeSindDeutsch(unittest.IsolatedAsyncioTestCase):
    ADMIN = SimpleNamespace(id="u1", role=UserRole.ADMIN)

    def _pruefe(self, fehler: HTTPException, status: int):
        self.assertEqual(fehler.status_code, status)
        self.assertEqual(ENGLISCH.findall(str(fehler.detail)), [], fehler.detail)
        self.assertNotRegex(str(fehler.detail), r"\b(not found|denied|only)\b")

    async def test_unbekannter_zeitplan(self):
        from app.api.schedules import _get_schedule

        with self.assertRaises(HTTPException) as ctx:
            await _get_schedule(_db_ohne_treffer(), "gibt-es-nicht")
        self._pruefe(ctx.exception, 404)

    async def test_unbekannte_vorlage(self):
        from app.api import templates

        with self.assertRaises(HTTPException) as ctx:
            await templates.get_template(999, user=self.ADMIN, db=_db_ohne_treffer())
        self._pruefe(ctx.exception, 404)


if __name__ == "__main__":
    unittest.main()
