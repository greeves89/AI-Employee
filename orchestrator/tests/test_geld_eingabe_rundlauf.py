"""Geldeingabe in der Anzeigewährung — Rundlauf ``toUsd`` ↔ Anzeige (#896).

Bis v1.362 nahm das Budgetfeld USD an, während daneben alles in € stand. Jetzt
wird in der Anzeigewährung eingegeben und beim Speichern umgerechnet
(``frontend/src/lib/waehrung.ts``). Geprüft wird die echte TypeScript-Datei mit
Node (Typen werden beim Laden entfernt) — fehlt Node, wird übersprungen.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

DATEI = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "waehrung.ts"

SKRIPT = """
const w = await import(%s);
const eur = {currency: "EUR", rate: 0.92};
const usd = {currency: "USD", rate: 0.92};
const out = {};
// Rundlauf: was als 10,00 € gespeichert wurde, steht danach wieder als 10.00 im Feld
out.rundlauf_eur = w.alsEingabe(w.ausEingabe("10", eur), eur);
out.rundlauf_komma = w.alsEingabe(w.ausEingabe("12,34", eur), eur);
out.rundlauf_usd = w.alsEingabe(w.ausEingabe("7.5", usd), usd);
out.usd_von_10_eur = w.ausEingabe("10", eur);
out.leer = w.ausEingabe("  ", eur);
out.negativ = w.ausEingabe("-1", eur) === undefined;
out.unsinn = w.ausEingabe("abc", eur) === undefined;
out.vorbelegung_null = w.alsEingabe(null, eur);
out.zeichen = [w.waehrungsZeichen(eur), w.waehrungsZeichen(usd)];
out.code = [w.waehrungsCode(eur), w.waehrungsCode(usd)];
out.kaputter_kurs = w.waehrungsCode({currency: "EUR", rate: 0});
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "Node nicht installiert")
class Rundlauf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lauf = subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", "--input-type=module",
             "-e", SKRIPT % json.dumps(DATEI.as_uri())],
            capture_output=True, text=True, timeout=60,
        )
        if lauf.returncode != 0:
            raise AssertionError(lauf.stderr)
        cls.out = json.loads(lauf.stdout)

    def test_rundlauf(self):
        self.assertEqual(self.out["rundlauf_eur"], "10.00")
        self.assertEqual(self.out["rundlauf_komma"], "12.34")
        self.assertEqual(self.out["rundlauf_usd"], "7.50")

    def test_gespeichert_wird_usd(self):
        self.assertAlmostEqual(self.out["usd_von_10_eur"], 10 / 0.92, places=9)

    def test_leer_heisst_kein_limit_ungueltig_wird_abgelehnt(self):
        self.assertIsNone(self.out["leer"])
        self.assertTrue(self.out["negativ"])
        self.assertTrue(self.out["unsinn"])
        self.assertEqual(self.out["vorbelegung_null"], "")

    def test_beschriftung_aus_der_konfiguration(self):
        self.assertEqual(self.out["zeichen"], ["€", "$"])
        self.assertEqual(self.out["code"], ["EUR", "USD"])
        self.assertEqual(self.out["kaputter_kurs"], "USD")


if __name__ == "__main__":
    unittest.main()
