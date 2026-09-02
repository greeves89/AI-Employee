"""Zwei Anzeigefehler, die die Oberflaeche unbrauchbar machten.

**Bernsteinfarbene Hinweise im hellen Thema** (#664): Die Warn- und
Hinweisboxen setzen helle Bernsteintoene auf hellen Bernsteingrund. Im dunklen
Thema — der Vorgabe — faellt das nicht auf; wer auf hell umschaltet, liest
nichts mehr. Gemeldet am Hinweis „Interne Adresse zulassen" beim Anlegen einer
MCP-Anbindung. Es waren nicht zwoelf Stellen, sondern 243 in 75 Dateien.

**Das Benachrichtigungsfeld** (#677): Es lag `absolute left-full` in einem
Container, dessen Eltern an BEIDEN Einbaustellen ihren Ueberlauf verbergen. Es
wurde gezeichnet und im selben Bild weggeschnitten — der Knopf sah aus wie tot,
obwohl er tat, was er sollte. Es gab in der ganzen Anwendung keine Stelle, an
der das Feld je sichtbar war.
"""

import re
import unittest
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
_GLOCKE = (_FRONTEND / "components" / "layout" / "notification-bell.tsx").read_text()

#: Helle Bernsteintoene ohne Gegenstueck fuers helle Thema.
_OHNE_HELLVARIANTE = re.compile(r"(?<!dark:)\btext-amber-[1-4]00\b")


def _tsx_dateien():
    return [p for p in _FRONTEND.rglob("*.tsx")]


class BernsteinTexteSindInBeidenThemenLesbarTests(unittest.TestCase):
    def test_keine_helle_schrift_ohne_gegenstueck(self):
        funde = []
        for p in _tsx_dateien():
            for nr, zeile in enumerate(p.read_text().splitlines(), 1):
                if "dark:text-amber" in zeile:
                    continue
                if _OHNE_HELLVARIANTE.search(zeile):
                    funde.append(f"{p.relative_to(_FRONTEND)}:{nr}")
        self.assertEqual(
            funde, [],
            "Helle Bernsteinschrift ohne Variante fuers helle Thema — dort steht "
            "sie auf hellem Grund und ist unlesbar. Muster: "
            "`text-amber-700 dark:text-amber-400`:\n" + "\n".join(funde[:30]),
        )

    def test_das_etablierte_muster_wird_benutzt(self):
        treffer = sum(p.read_text().count("text-amber-700 dark:text-amber-")
                      for p in _tsx_dateien())
        self.assertGreater(treffer, 200, "Die Umstellung fehlt weitgehend")


class DasBenachrichtigungsfeldWirdNichtMehrWeggeschnittenTests(unittest.TestCase):
    def test_es_haengt_am_dokument_statt_im_seitenstreifen(self):
        """Der Kern: kein Elternteil kann es mehr abschneiden."""
        self.assertIn("createPortal(", _GLOCKE)
        self.assertIn("document.body,", _GLOCKE)

    def test_es_steht_fest_statt_relativ_zum_streifen(self):
        self.assertNotIn('className="absolute left-full ml-2 bottom-0', _GLOCKE)
        block = _GLOCKE.split("createPortal(", 1)[1][:600]
        self.assertIn('className="fixed', block)

    def test_die_lage_kommt_vom_knopf(self):
        """Ein festes Feld ohne berechnete Lage klebt in der Ecke."""
        self.assertIn("getBoundingClientRect()", _GLOCKE)
        self.assertIn("setLage({", _GLOCKE)

    def test_auf_schmalen_geraeten_bleibt_es_im_bild(self):
        block = _GLOCKE.split("const lageBerechnen", 1)[1][:900]
        self.assertIn("window.innerWidth", block)
        self.assertIn("Math.min(360", block)

    def test_es_wandert_beim_rollen_mit(self):
        self.assertIn('window.addEventListener("scroll", nach, true)', _GLOCKE)
        self.assertIn('window.removeEventListener("scroll", nach, true)', _GLOCKE)

    def test_ein_klick_ins_feld_schliesst_es_nicht(self):
        """Seit dem Portal liegt das Feld ausserhalb des Knopf-Teilbaums — ohne
        eigene Pruefung waere jeder Klick darin ein Klick „ausserhalb", und
        „alle gelesen" oder „loeschen" wuerden das Feld wegklappen."""
        self.assertIn("const imFeld = feldRef.current?.contains(ziel)", _GLOCKE)
        self.assertIn("if (!imKnopf && !imFeld)", _GLOCKE)

    def test_es_liegt_ueber_dem_seitenstreifen(self):
        block = _GLOCKE.split("createPortal(", 1)[1][:600]
        self.assertIn("z-[100]", block)

    def test_die_beiden_alten_zweige_bleiben(self):
        """Ausdruecklich nicht im Auftrag: die eingeklappte Variante und die
        Symbol-Variante wegzuraeumen."""
        self.assertIn('variant === "sidebar" && collapsed', _GLOCKE)
        self.assertIn('variant?: "icon" | "sidebar"', _GLOCKE)


if __name__ == "__main__":
    unittest.main()
