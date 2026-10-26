"""Symbole der Bridge: App-Icon in beiden Builds, ein Menueleisten-/Tray-Bild je Zustand.

Vorher stand in beiden Specs ``icon=None`` — die App trug das Standard-Symbol von
PyInstaller, die Menueleiste nur Textzeichen.
"""

import re
import sys
import unittest
from pathlib import Path

HIER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIER))
import tray_app  # noqa: E402


class Zustand(unittest.TestCase):
    def test_zuordnung(self):
        self.assertEqual(tray_app.tray_zustand("connected", False, False), "verbunden")
        self.assertEqual(tray_app.tray_zustand("connected", False, True), "sieht_mit")
        self.assertEqual(tray_app.tray_zustand("connecting", False, True), "verbindet",
                         "Ohne Verbindung sieht niemand mit")
        self.assertEqual(tray_app.tray_zustand("disconnected", True, False), "verbindet")
        self.assertEqual(tray_app.tray_zustand("error: neu anmelden", False, False), "getrennt")


class Dateien(unittest.TestCase):
    def test_jedes_zustandsbild_ist_da(self):
        for zustand in tray_app.SYMBOL_ZUSTAENDE:
            for art in ("menueleiste", "tray"):
                with self.subTest(art=art, zustand=zustand):
                    self.assertIsNotNone(tray_app._asset_pfad(f"{art}-{zustand}.png"))

    def test_specs_nennen_vorhandene_symbole(self):
        for spec, endung in (("bridge_macos.spec", ".icns"), ("bridge_windows.spec", ".ico")):
            with self.subTest(spec=spec):
                text = (HIER / spec).read_text(encoding="utf-8")
                treffer = re.search(r"icon='([^']+)'", text)
                self.assertIsNotNone(treffer, "icon=None — die App trüge das PyInstaller-Symbol")
                self.assertTrue(treffer.group(1).endswith(endung))
                self.assertTrue((HIER / treffer.group(1)).exists(), treffer.group(1))
                self.assertIn("'assets'", text, "Zustandsbilder muessen ins Bundle")


if __name__ == "__main__":
    unittest.main()


class Hauptfenster(unittest.TestCase):
    """Was Mac- und Windows-Hauptfenster zeigen — eine Ableitung fuer beide."""

    CFG = {"url": "https://agents.example.com", "session": "a29e61c61b6c",
           "allowed_capabilities": ["screenshots", "observe"], "mitdenken": False}

    def test_verbunden_mit_mitsehen(self):
        i = tray_app.hauptfenster_inhalt(self.CFG, ("Verbunden", "green"), True, True, 2)
        self.assertEqual(i["aktion"], "Trennen")
        self.assertIn("agents.example.com", i["meta"])
        self.assertIn("Sitzung a29e61c6", i["meta"])
        self.assertTrue(i["sehen"].startswith("Sieht mit · 2 Regeln"))
        self.assertTrue(i["sieht_mit"])
        self.assertEqual(i["mitdenken_knopf"], "Mitdenken einschalten")

    def test_ohne_freigabe_kein_mitsehen(self):
        cfg = {**self.CFG, "allowed_capabilities": ["screenshots"]}
        i = tray_app.hauptfenster_inhalt(cfg, ("Nicht verbunden", "gray"), False, True)
        self.assertEqual(i["aktion"], "Verbinden")
        self.assertFalse(i["sieht_mit"], "Ohne Freigabe zeigt das Fenster nie „sieht mit“")
        self.assertIn("Berechtigungen", i["sehen"])
        self.assertEqual(i["freigaben"], "1 Freigabe")
