"""Der Selbsttest meldet sich nur, wenn sich etwas geaendert hat.

Er laeuft alle paar Stunden. Bis v1.336.0 erzeugte JEDER Lauf eine
Benachrichtigung — auf dem Pi 557-mal "Self-Test: 27/28 passed". Das fiel nur
nicht auf, weil Systemmeldungen fuer niemanden sichtbar waren. Seit sie bei
den Administratoren ankommen, waere das eine Flut von rund 25 identischen
Meldungen am Tag; die eine wichtige ginge darin unter.

Gemeldet wird also: der erste Lauf, ein Wechsel zwischen bestanden und
fehlgeschlagen, und eine veraenderte Zahl von Fehlschlaegen (es wird besser
oder schlimmer). Gleicher Stand wie zuletzt: Ruhe.
"""

import unittest

from app.services.self_test_service import _soll_melden


class SelbsttestMeldungTest(unittest.TestCase):

    def test_erster_lauf_wird_gemeldet(self):
        self.assertTrue(_soll_melden(vorher=None, fehlgeschlagen=0))

    def test_unveraendert_gruen_bleibt_still(self):
        self.assertFalse(_soll_melden(vorher=0, fehlgeschlagen=0))

    def test_unveraendert_rot_bleibt_still(self):
        """Derselbe Fehler zum zwanzigsten Mal ist keine Neuigkeit."""
        self.assertFalse(_soll_melden(vorher=2, fehlgeschlagen=2))

    def test_von_gruen_nach_rot_wird_gemeldet(self):
        self.assertTrue(_soll_melden(vorher=0, fehlgeschlagen=1))

    def test_von_rot_nach_gruen_wird_gemeldet(self):
        """Die Entwarnung ist genauso wichtig wie der Alarm."""
        self.assertTrue(_soll_melden(vorher=3, fehlgeschlagen=0))

    def test_mehr_fehlschlaege_werden_gemeldet(self):
        self.assertTrue(_soll_melden(vorher=1, fehlgeschlagen=6))

    def test_weniger_fehlschlaege_werden_gemeldet(self):
        self.assertTrue(_soll_melden(vorher=6, fehlgeschlagen=2))


if __name__ == "__main__":
    unittest.main()
