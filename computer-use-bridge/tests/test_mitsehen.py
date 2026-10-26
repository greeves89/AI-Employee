"""Mitsehen (#1019) in der Bridge selbst — mit echten Bildern (Pillow) und auf der
echten Plattform: Der Build-Workflow fuehrt diese Tests auf macOS UND Windows aus,
bevor er die App baut.

Beobachter mit gestellter Uhr (kein Thread), private Fenster, Sperrzone fuers
Overlay, Bildaufbereitung und das Lesen des Vordergrundfensters.
"""

import base64
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge  # noqa: E402


class _Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _bild(farbe):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 200), (255, 255, 255))
    if farbe:
        ImageDraw.Draw(img).rectangle((0, 0, 160, 200), fill=farbe)
    return img


class Beobachter(unittest.TestCase):
    """Der Beobachter in der Bridge — ohne Thread, mit gestellter Uhr."""

    def setUp(self):
        self.ereignisse = []
        self.uhr = _Uhr()
        self.vorn = {"app": "Google Chrome", "titel": "Start"}
        self.bild = _bild(None)
        self.o = bridge.ScreenObserver(self.ereignisse.append, bild_holen=lambda: self.bild,
                                       vordergrund_holen=lambda: dict(self.vorn), uhr=self.uhr)

    def _schritte(self, n, sekunden=3.0):
        for _ in range(n):
            self.uhr.t += sekunden
            self.o.schritt()

    def typen(self):
        return [e["type"] for e in self.ereignisse]

    def test_regel_trifft_einmal_je_fenster(self):
        self.o.konfigurieren({"beobachten": True, "regeln": [
            {"id": 7, "wenn_titel": "YouTube Studio", "angebot": "x", "aktiv": True}]})
        self.vorn["titel"] = "Inhalte - YouTube Studio"
        self._schritte(3)
        self.assertEqual(self.typen(), ["rule_hit"])
        self.assertEqual(self.ereignisse[0]["regel_id"], 7)
        # Weg und zurück innerhalb der Ruhezeit: kein zweites Angebot.
        self.vorn["titel"] = "Gmail"
        self._schritte(1)
        self.vorn["titel"] = "Inhalte - YouTube Studio"
        self._schritte(1)
        self.assertEqual(self.typen(), ["rule_hit"])
        # Nach der Ruhezeit wieder.
        self.vorn["titel"] = "Gmail"
        self._schritte(1, sekunden=bridge.ScreenObserver.REGEL_RUHE_SECS)
        self.vorn["titel"] = "Inhalte - YouTube Studio"
        self._schritte(1)
        self.assertEqual(self.typen(), ["rule_hit", "rule_hit"])

    def test_gestoppt_bietet_nichts_an(self):
        self.o.konfigurieren({"beobachten": True, "mitdenken": True, "gestoppt": True, "regeln": [
            {"id": 1, "wenn_app": "Chrome", "angebot": "x"}]})
        self._schritte(30)
        self.assertEqual(self.ereignisse, [])

    def test_ohne_mitdenken_keine_bilder(self):
        self.o.konfigurieren({"beobachten": True, "mitdenken": False})
        geholt = []
        self.o._bild_holen = lambda: geholt.append(1) or self.bild
        self._schritte(30)
        self.assertEqual(self.ereignisse, [])
        self.assertEqual(geholt, [], "Ohne Mitdenken wird nicht einmal fotografiert")

    def test_mitdenken_erst_wenn_das_bild_ruhig_ist(self):
        self.o.konfigurieren({"beobachten": True, "mitdenken": True})
        # Takt 3 s, Ruhe 4 s: nach dem ersten Blick erst im dritten Takt.
        self._schritte(2)
        self.assertEqual(self.ereignisse, [], "Bild noch nicht lange genug ruhig")
        self._schritte(1)
        self.assertEqual(self.typen(), ["observation"])
        blick = self.ereignisse[0]
        self.assertEqual(blick["media_type"], "image/jpeg")
        self.assertTrue(blick["bild_b64"])
        # Neuer Inhalt kurz danach: Mindestabstand hält zurück.
        self.bild = _bild((0, 0, 0))
        self._schritte(5)
        self.assertEqual(self.typen(), ["observation"])
        self._schritte(1, sekunden=bridge.ScreenObserver.MIN_ABSTAND_SECS)
        self.assertEqual(self.typen(), ["observation", "observation"])

    def test_gleiches_bild_kein_zweiter_blick(self):
        self.o.konfigurieren({"beobachten": True, "mitdenken": True})
        self._schritte(2)
        self._schritte(10, sekunden=bridge.ScreenObserver.MIN_ABSTAND_SECS)
        self.assertEqual(self.typen(), ["observation"])

    def test_mitdenken_braucht_beobachten(self):
        self.o.konfigurieren({"beobachten": False, "mitdenken": True})
        self.assertFalse(self.o.mitdenken)


class PrivateFenster(unittest.TestCase):
    def test_kein_bild_in_privaten_fenstern(self):
        geholt = []
        uhr = _Uhr()
        o = bridge.ScreenObserver(lambda e: None, bild_holen=lambda: geholt.append(1) or _bild(None),
                                  vordergrund_holen=lambda: {"app": "Bitwarden", "titel": "Tresor"}, uhr=uhr)
        o.konfigurieren({"beobachten": True, "mitdenken": True})
        for _ in range(10):
            uhr.t += 30
            o.schritt()
        self.assertEqual(geholt, [])


class Bildaufbereitung(unittest.TestCase):
    def test_fingerabdruck_trennt_neuen_inhalt_von_kleinkram(self):
        from PIL import ImageDraw

        a = _bild(None)
        b = _bild(None)
        ImageDraw.Draw(b).point((5, 5), fill=(0, 0, 0))        # Mauszeiger-Kleinkram
        c = _bild((0, 0, 0))                                    # neuer Inhalt
        fa, fb, fc = (bridge.bild_fingerabdruck(x) for x in (a, b, c))
        self.assertLessEqual(bridge.bild_abstand(fa, fb), bridge.ScreenObserver.SCHWELLE_BITS)
        self.assertGreater(bridge.bild_abstand(fa, fc), bridge.ScreenObserver.SCHWELLE_BITS)

    def test_jpeg_ist_klein_und_gueltig(self):
        from PIL import Image

        gross = Image.new("RGB", (2880, 1800), (200, 200, 200))
        b64 = bridge._bild_als_jpeg(gross)
        bild = Image.open(io.BytesIO(base64.b64decode(b64)))
        self.assertEqual(bild.format, "JPEG")
        self.assertEqual(bild.width, 1280)


class Sperrzone(unittest.TestCase):
    def tearDown(self):
        bridge.OVERLAY_SPERRZONE = None

    def test_klick_ins_overlay_wird_abgewiesen(self):
        """Durch den echten Dispatcher-Zweig, mit gestellter Maus."""
        klicks = []
        d = bridge.CommandDispatcher.__new__(bridge.CommandDispatcher)
        d._coord_scale = (1.0, 1.0)
        d._coord_offset = (0, 0)
        d._ctrl = type("Maus", (), {"click": lambda self, *a, **k: klicks.append(a)})()
        bridge.OVERLAY_SPERRZONE = (400, 0, 640, 170)
        antwort = d.dispatch({"action": "click", "params": {"x": 500, "y": 60}})
        self.assertFalse(antwort.get("ok"))
        self.assertEqual(klicks, [])
        antwort = d.dispatch({"action": "click", "params": {"x": 100, "y": 600}})
        self.assertTrue(antwort.get("ok"), antwort)
        self.assertEqual(len(klicks), 1)


class Abdecken(unittest.TestCase):
    def tearDown(self):
        bridge.OVERLAY_SPERRZONE = None
        bridge.OVERLAY_ABDECKEN = False

    def test_flaeche_wird_grau(self):
        from PIL import Image
        from unittest.mock import patch

        bridge.OVERLAY_SPERRZONE = (100, 0, 200, 50)
        img = Image.new("RGB", (800, 400), (255, 0, 0))
        with patch.object(bridge, "_logical_screen_size", return_value=(400, 200)):
            bridge._overlay_abdecken(img)
        self.assertEqual(img.getpixel((400, 50)), (128, 128, 128), "Mitte der Flaeche (Retina x2)")
        self.assertEqual(img.getpixel((50, 50)), (255, 0, 0), "daneben unberuehrt")


class Vordergrund(unittest.TestCase):
    def test_liest_ohne_fehler(self):
        """Auf dem Build-Runner (macOS/Windows) laeuft der echte Weg — er darf nie werfen."""
        vorn = bridge.vordergrund()
        self.assertEqual(set(vorn), {"app", "titel"})
        self.assertIsInstance(vorn["app"], str)
        self.assertIsInstance(vorn["titel"], str)


if __name__ == "__main__":
    unittest.main()
