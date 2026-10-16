"""Platz im Büro: zufällig, nie doppelt, ein neuer Bereich erst wenn alle voll sind."""

import random
import unittest

from app.core import buero


class PlatzWaehlen(unittest.TestCase):
    def test_nie_ein_belegter_platz(self):
        belegt = {(b, p) for b in range(4) for p in range(6)} - {(2, 3)}
        self.assertEqual(buero.platz_waehlen(belegt), {"bereich": 2, "platz": 3})

    def test_voll_beginnt_einen_neuen_bereich(self):
        belegt = {(b, p) for b in range(4) for p in range(6)}
        self.assertEqual(buero.platz_waehlen(belegt, random.Random(1))["bereich"], 4)

    def test_zufaellig_verteilt(self):
        zufall = random.Random(7)
        plaetze = {tuple(buero.platz_waehlen(set(), zufall).values()) for _ in range(40)}
        self.assertGreater(len({b for b, _ in plaetze}), 1)

    def test_kaputte_angaben_zaehlen_nicht(self):
        for cfg in (None, {}, {"buero": "x"}, {"buero": {"bereich": 0, "platz": 9}},
                    {"buero": {"bereich": -1, "platz": 0}}, {"buero": {"bereich": "1", "platz": 0}}):
            self.assertIsNone(buero.platz_aus(cfg))
        self.assertEqual(buero.platz_aus({"buero": {"bereich": 1, "platz": 5}}), (1, 5))


if __name__ == "__main__":
    unittest.main()
