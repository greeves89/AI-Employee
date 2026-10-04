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
// Summe wie angezeigt (#896): drei Zeilen zu je 2,896 $ stehen als 2,90 da —
// die Liste ergibt 8,70, die exakte Summe 8,688 hiesse 8,69.
const zeilen = [2.896, 2.896, 2.896];
const cent = (usd, cfg) => Math.round(w.vonUsd(usd, cfg) * 100);
out.summe_usd_cent = cent(w.summeDerZeilen(zeilen, usd), usd);
out.zeilen_usd_cent = zeilen.reduce((s, z) => s + cent(z, usd), 0);
const zeilenEur = [1.234, 5.678, 0.111, 0.004];
out.summe_eur_cent = cent(w.summeDerZeilen(zeilenEur, eur), eur);
out.zeilen_eur_cent = zeilenEur.reduce((s, z) => s + cent(z, eur), 0);
out.summe_leer = w.summeDerZeilen([], eur);
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

    def test_summe_passt_zur_liste(self):
        """Budget-Seite: „Kosten diesen Monat“ 8,70 € neben Zeilen, die 8,69 €
        ergaben (#896). Die Summe ist jetzt die der angezeigten Zeilen."""
        self.assertEqual(self.out["summe_usd_cent"], 870)
        self.assertEqual(self.out["summe_usd_cent"], self.out["zeilen_usd_cent"])
        self.assertEqual(self.out["summe_eur_cent"], self.out["zeilen_eur_cent"])
        self.assertEqual(self.out["summe_leer"], 0)


if __name__ == "__main__":
    unittest.main()
