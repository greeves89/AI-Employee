"""Mikrofon unter Hardened Runtime, stilles Mikrofon erkennen, Overlay-Lage merken."""

import plistlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))
import tray_app  # noqa: E402


class Berechtigung(unittest.TestCase):
    def test_mikrofon_berechtigung_im_signierten_bundle(self):
        # Ohne sie liefert macOS einer Hardened-Runtime-App nur Stille — ohne Fehler,
        # obwohl die Freigabe in den Systemeinstellungen an ist (bis 1.378.14).
        with open(BRIDGE / "entitlements.plist", "rb") as f:
            rechte = plistlib.load(f)
        self.assertIs(rechte.get("com.apple.security.device.audio-input"), True)


class StillesMikrofon(unittest.TestCase):
    def test_nur_nullen_heisst_stumm(self):
        mic = tray_app._MicStreamer()
        mic.bloecke, mic.spitze = 25, 0
        self.assertTrue(mic.stumm())

    def test_rauschen_ist_nicht_stumm(self):
        mic = tray_app._MicStreamer()
        mic.bloecke, mic.spitze = 25, 3
        self.assertFalse(mic.stumm())

    def test_zu_frueh_kein_urteil(self):
        mic = tray_app._MicStreamer()
        mic.bloecke, mic.spitze = 5, 0
        self.assertFalse(mic.stumm())


class OverlayLage(unittest.TestCase):
    def test_ohne_lage_oben_mittig(self):
        self.assertIsNone(tray_app.overlay_lage_lesen({}))
        self.assertIsNone(tray_app.overlay_lage_lesen({"overlay_lage": "kaputt"}))
        self.assertIsNone(tray_app.overlay_lage_lesen({"overlay_lage": [1]}))

    def test_merken_und_lesen(self):
        cfg = {}
        with patch.object(tray_app, "save_config") as speichern:
            tray_app.overlay_lage_merken(cfg, (-1200.4, 980.6))
            tray_app.overlay_lage_merken(cfg, (-1200.2, 981.0))   # gleiche Lage: nicht noch mal
        self.assertEqual(speichern.call_count, 1)
        self.assertEqual(tray_app.overlay_lage_lesen(cfg), (-1200.0, 981.0))


if __name__ == "__main__":
    unittest.main()
