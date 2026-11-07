"""Statuszeile je Projekt (core/projekte.status) — Rangfolge nach dem, was ein Mensch tun muss.

blockiert (Konflikt, gescheiterter Lauf im Review) > wartet (Rückfrage, Abnahme, PR zum
Mergen) > arbeitet (Läufe, Karten in Arbeit) > ruht. Jede Bedingung wird einzeln umgeschaltet
(MC/DC), damit keine still die andere überdeckt.
"""
import unittest

from app.core.projekte import status


class StatusTests(unittest.TestCase):
    def test_einzelne_bedingungen(self):
        faelle = [
            ({}, "ruht", "Ruht"),
            ({"bereit": 3}, "ruht", "3 bereit"),
            ({"in_arbeit": 2}, "arbeitet", "Arbeitet: arbeitet (2)"),
            ({"laeufe": 1, "in_arbeit": 2}, "arbeitet", "arbeitet (1)"),
            ({"triage": 1}, "wartet", "1 Rückfrage an dich"),
            ({"triage": 2}, "wartet", "2 Rückfragen an dich"),
            ({"pruefung": 2}, "wartet", "2 zur Abnahme"),
            ({"pruefung": 1, "wartet_auf_merge": 1}, "wartet", "1 PR zum Mergen"),
            ({"konflikte": 1}, "blockiert", "1 Konflikt"),
            ({"pruefung": 1, "gescheitert": 1}, "blockiert", "1 gescheitert"),
        ]
        for kennzahlen, stufe, teil in faelle:
            with self.subTest(kennzahlen=kennzahlen):
                s = status(kennzahlen)
                self.assertEqual(s["stufe"], stufe)
                self.assertIn(teil, s["text"])

    def test_rangfolge_und_alles_genannt(self):
        s = status({"konflikte": 1, "triage": 1, "pruefung": 2, "in_arbeit": 1})
        self.assertEqual(s["stufe"], "blockiert", "Blockade geht vor")
        for teil in ("1 Konflikt", "1 Rückfrage", "2 zur Abnahme", "arbeitet (1)"):
            self.assertIn(teil, s["text"])
        self.assertLess(s["text"].index("Konflikt"), s["text"].index("Rückfrage"), "Dringendstes zuerst")
        self.assertEqual(status({"triage": 1, "in_arbeit": 3})["stufe"], "wartet", "Warten vor Arbeiten")

    def test_gescheitert_und_merge_nicht_doppelt_als_abnahme(self):
        s = status({"pruefung": 3, "gescheitert": 1, "wartet_auf_merge": 1})
        self.assertIn("1 zur Abnahme", s["text"])

    def test_leere_oder_fehlende_kennzahlen(self):
        self.assertEqual(status(None)["stufe"], "ruht")
        self.assertEqual(status({"triage": None})["stufe"], "ruht")


if __name__ == "__main__":
    unittest.main()
