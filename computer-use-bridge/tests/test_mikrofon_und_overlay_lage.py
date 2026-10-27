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


class Abtastrate(unittest.TestCase):
    """Bluetooth-Kopfhoerer lehnen 16/24 kHz oft ab — die Bridge rechnet selbst um."""

    def test_umrechnen_laenge_und_werte(self):
        import numpy as np
        ton = (np.sin(np.linspace(0, 20, 4800)) * 10000).astype(np.int16)   # 100 ms bei 48 kHz
        klein = tray_app.pcm_umrechnen(ton, 48000, 16000)
        self.assertEqual(len(klein), 1600)
        self.assertEqual(klein.dtype, np.int16)
        self.assertGreater(int(abs(klein).max()), 9000, "Lautstaerke bleibt erhalten")
        self.assertIs(tray_app.pcm_umrechnen(ton, 16000, 16000), ton)

    def test_mikrofon_faellt_auf_geraeterate_zurueck(self):
        import numpy as np

        geoeffnet, gesendet = [], []

        class Strom:
            def __init__(self, samplerate, callback, **_k):
                if samplerate == 16000:
                    raise RuntimeError("Error opening InputStream: Invalid sample rate")
                geoeffnet.append(samplerate)
                self.cb = callback

            def start(self):
                self.cb(np.full((samplerate_block := 2400, 1), 500, dtype=np.int16), 0, None, None)

            def stop(self):
                pass

            def close(self):
                pass

        neu_eingelesen = []
        sd = type(sys)("sounddevice")
        sd.InputStream = Strom
        sd.query_devices = lambda kind=None: {"name": "Kopfhoerer", "default_samplerate": 24000}
        sd._terminate = lambda: neu_eingelesen.append(1)
        sd._initialize = lambda: None
        with patch.dict(sys.modules, {"sounddevice": sd}):
            mic = tray_app._MicStreamer()
            self.assertEqual(mic.start(gesendet.append), "")
            mic.stop()
        self.assertEqual(geoeffnet, [24000])
        self.assertTrue(neu_eingelesen, "Geraeteliste wird neu eingelesen")
        self.assertEqual(len(gesendet[0]), 1600 * 2, "100 ms bei 16 kHz an die Sitzung")


if __name__ == "__main__":
    unittest.main()
