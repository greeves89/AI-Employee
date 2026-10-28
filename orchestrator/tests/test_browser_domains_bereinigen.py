"""Die Freigabeliste des Agenten-Browsers ist Nutzereingabe (#828, Schritt 5).

Sie geht als Umgebungsvariable in einen Container und wird dort an Kommas
getrennt. Zwei Dinge duerfen deshalb nicht passieren: ein Eintrag, der sich
unterwegs in zwei verwandelt, und ein Eintrag, der aussieht wie eine
Freigabe, aber nie passt -- beim Abgleich im Agenten wird ausschliesslich der
HOSTNAME verglichen.

Stdlib-only, damit das auch ohne das Umfeld der App laeuft.
"""

import unittest

from app.core.browser_domains import MAX_EINTRAEGE, UngueltigeDomain, bereinigen


class FreigabelisteBereinigen(unittest.TestCase):

    def test_leer_heisst_unbeschraenkt(self):
        self.assertEqual([], bereinigen(None))
        self.assertEqual([], bereinigen([]))
        self.assertEqual([], bereinigen(["", "   ", None]))

    def test_normalisiert_und_entdoppelt(self):
        self.assertEqual(
            ["example.com", "a.example.org"],
            bereinigen(["  Example.COM ", ".example.com", "a.example.org"]))

    def test_komma_wird_abgewiesen_statt_zwei_eintraege_zu_werden(self):
        with self.assertRaises(UngueltigeDomain):
            bereinigen(["example.com,angreifer.invalid"])

    def test_zeilenumbruch_und_leerzeichen_werden_abgewiesen(self):
        for boese in ["example.com\nangreifer.invalid", "exa mple.com",
                      "example.com\tx"]:
            with self.subTest(wert=boese):
                with self.assertRaises(UngueltigeDomain):
                    bereinigen([boese])

    def test_was_nie_passen_wuerde_wird_nicht_still_uebernommen(self):
        """Schema, Pfad, Benutzerangabe, Platzhalter -- alles kein Hostname.

        Der Agent vergleicht den Host. ``https://example.com`` als Eintrag
        gaebe eine Liste, die nichts freigibt, und der Besitzer wuerde sich
        auf sie verlassen.
        """
        for boese in ["https://example.com", "example.com/pfad",
                      "nutzer@example.com", "*.example.com", "example.com?x=1"]:
            with self.subTest(wert=boese):
                with self.assertRaises(UngueltigeDomain):
                    bereinigen([boese])

    def test_ohne_punkt_nur_localhost(self):
        self.assertEqual(["localhost"], bereinigen(["localhost"]))
        with self.assertRaises(UngueltigeDomain):
            bereinigen(["intern"])

    def test_obergrenzen(self):
        self.assertEqual(MAX_EINTRAEGE,
                         len(bereinigen([f"h{i}.example.com" for i in range(MAX_EINTRAEGE)])))
        with self.assertRaises(UngueltigeDomain):
            bereinigen([f"h{i}.example.com" for i in range(MAX_EINTRAEGE + 1)])
        with self.assertRaises(UngueltigeDomain):
            bereinigen(["a" * 250 + ".example.com"])
