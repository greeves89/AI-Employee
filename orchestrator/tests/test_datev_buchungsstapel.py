"""DATEV-Export im Skill „buchhaltung-vorkontieren“: Bruttobetrag bei BU-Schlüssel (#894).

Befund aus dem Markttest: Die DATEV-CSV der Vorlage „Buchhaltung“ trug bei
BU-Schlüssel 9 den NETTObetrag. DATEV rechnet die Steuer bei einem
Steuerschlüssel aus dem Umsatz heraus — der Umsatz muss also brutto sein,
sonst fehlen Vorsteuer und Betrag.

Der Skill enthält deshalb einen Prüfschritt als ausführbaren Code, den der
Agent vor der Ausgabe laufen lässt. Dieser Test führt GENAU diesen Code aus
dem Skill aus — nicht eine Kopie davon.
"""

import re
import unittest
from pathlib import Path

SKILL = (Path(__file__).resolve().parents[1] / "app" / "skills_mitgeliefert"
         / "buchhaltung-vorkontieren" / "SKILL.md")


def _pruefschritt() -> dict:
    """Den Code-Block „Prüfschritt DATEV“ aus dem Skill laden und ausführen."""
    text = SKILL.read_text(encoding="utf-8")
    bloecke = re.findall(r"```python\n(.*?)```", text, re.S)
    passende = [b for b in bloecke if "# Prüfschritt DATEV" in b]
    assert len(passende) == 1, "Genau ein Prüfschritt-Block im Skill erwartet"
    ns: dict = {}
    exec(compile(passende[0], str(SKILL), "exec"), ns)  # noqa: S102 — eigener Skill-Code
    return ns


def _zeile(umsatz, sh="H", bu="9", netto="100,00", **mehr):
    return {"Umsatz": umsatz, "Soll/Haben": sh, "BU": bu, "Netto": netto, **mehr}


class PruefschrittTests(unittest.TestCase):
    def setUp(self):
        self.kontrolle = _pruefschritt()["kontrolle_buchungsstapel"]

    def test_nettobetrag_mit_bu_9_ist_ein_fehler(self):
        fehler = self.kontrolle([_zeile("100,00")])
        self.assertEqual(len(fehler), 1)
        self.assertIn("119,00", fehler[0])

    def test_bruttobetrag_mit_bu_9_ist_in_ordnung(self):
        self.assertEqual(self.kontrolle([_zeile("119,00")]), [])

    def test_sieben_prozent_vorsteuer_und_umsatzsteuer(self):
        self.assertEqual(self.kontrolle([_zeile("107,00", bu="8"), _zeile("107,00", sh="S", bu="2")]), [])
        self.assertEqual(len(self.kontrolle([_zeile("119,00", bu="2")])), 1)

    def test_umsatzsteuer_19_mit_bu_3(self):
        self.assertEqual(self.kontrolle([_zeile("1.190,00", sh="S", bu="3", netto="1.000,00")]), [])

    def test_rundung_um_einen_cent_wird_hingenommen(self):
        self.assertEqual(self.kontrolle([_zeile("119,01")]), [])

    def test_umsatz_ist_immer_positiv_richtung_ueber_soll_haben(self):
        self.assertEqual(len(self.kontrolle([_zeile("-119,00")])), 1)

    def test_soll_haben_kennzeichen_ist_pflicht(self):
        self.assertEqual(len(self.kontrolle([_zeile("119,00", sh="")])), 1)

    def test_ohne_bu_schluessel_keine_bruttopruefung(self):
        """Automatikkonto oder steuerfreier Vorgang: kein BU, keine Umrechnung."""
        self.assertEqual(self.kontrolle([_zeile("100,00", bu="")]), [])

    def test_sonderfaelle_wie_13b_bleiben_netto(self):
        """§ 13b und innergemeinschaftlicher Erwerb: Schlüssel nach Kontenplan, Betrag netto."""
        self.assertEqual(self.kontrolle([_zeile("100,00", bu="94")]), [])

    def test_automatikkonto_darf_keinen_bu_schluessel_tragen(self):
        self.assertEqual(len(self.kontrolle([_zeile("119,00", Automatikkonto="ja")])), 1)
        self.assertEqual(self.kontrolle([_zeile("119,00", bu="", Automatikkonto="ja")]), [])


class SkillTextTests(unittest.TestCase):
    def test_abschnitt_export_und_kanzlei_buchungsliste(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("## Export DATEV-Buchungsstapel", text)
        self.assertIn("Buchungsliste zum Import durch die Kanzlei", text)


if __name__ == "__main__":
    unittest.main()
