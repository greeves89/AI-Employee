"""Mehrere Monitore: der Agent sieht alle und klickt auf dem richtigen.

Gestellt: ein Hauptbildschirm 1440x900 bei (0,0) und daneben ein zweiter mit
1920x1080 bei (1440,0). Beide Bilder werden auf 1280 Breite verkleinert — der
Maszstab ist also je Monitor ein anderer (1,125 gegen 1,5). Ein Klick mit
``display=2`` muss mit dem Maszstab und Ursprung von Monitor 2 rechnen, auch
wenn das zuletzt aufgenommene Bild das des Hauptbildschirms war.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge  # noqa: E402

MONITORE = [
    {"number": 1, "id": 11, "primary": True, "x": 0, "y": 0, "width": 1440, "height": 900},
    {"number": 2, "id": 22, "primary": False, "x": 1440, "y": 0, "width": 1920, "height": 1080},
]


def _aufnahme(scale=1.0, display=None):
    m = next(b for b in MONITORE if b["number"] == (int(display) if display else 1))
    bild_w, bild_h = 1280, round(1280 * m["height"] / m["width"])
    return f"bild-{m['number']}", {
        "image_w": bild_w, "image_h": bild_h,
        "logical_w": m["width"], "logical_h": m["height"],
        "scale_x": m["width"] / bild_w, "scale_y": m["height"] / bild_h,
        "display": m["number"], "displays": MONITORE,
    }


class AlleMonitore(unittest.TestCase):
    def setUp(self):
        p1 = mock.patch.object(bridge, "capture_screenshot", side_effect=_aufnahme)
        p2 = mock.patch.object(bridge, "list_displays", return_value=MONITORE)
        p1.start(), p2.start()
        self.addCleanup(mock.patch.stopall)
        self.d = bridge.CommandDispatcher.__new__(bridge.CommandDispatcher)
        self.d._ctrl = mock.Mock()
        self.d._coord_scale, self.d._coord_offset, self.d._anzeigen = (1.0, 1.0), (0, 0), {}

    def _klick(self, **params):
        self.d._ctrl.click.reset_mock()
        with mock.patch.object(bridge, "in_overlay", return_value=False):
            self.assertEqual(self.d.dispatch({"action": "click", "params": params}), {"ok": True})
        return self.d._ctrl.click.call_args[0][:2]

    def test_ohne_wunsch_nur_der_hauptbildschirm(self):
        r = self.d.dispatch({"action": "screenshot", "params": {}})
        self.assertEqual(r["screenshot_b64"], "bild-1")
        self.assertNotIn("weitere", r)

    def test_alle_bildschirme_liefert_jeden_monitor(self):
        r = self.d.dispatch({"action": "screenshot", "params": {"alle_bildschirme": True}})
        self.assertEqual((r["display"], r["screenshot_b64"]), (1, "bild-1"))
        self.assertEqual([(w["display"], w["screenshot_b64"]) for w in r["weitere"]], [(2, "bild-2")])
        self.assertEqual(r["weitere"][0]["image_size"], {"w": 1280, "h": 720})

    def test_klick_rechnet_je_monitor(self):
        self.d.dispatch({"action": "screenshot", "params": {"alle_bildschirme": True}})
        # Monitor 2: Bildmitte 640/360 → 1440 + 640*1,5 = 2400, 360*1,5 = 540
        self.assertEqual(self._klick(x=640, y=360, display=2), (2400, 540))
        # Ohne display: weiter der Hauptbildschirm (Maszstab 1,125)
        self.assertEqual(self._klick(x=640, y=400), (720, 450))
        self.assertEqual(self._klick(x=640, y=400, display=1), (720, 450))

    def test_mit_display_bleibt_es_bei_einem_bild(self):
        r = self.d.dispatch({"action": "screenshot", "params": {"display": 2, "alle_bildschirme": True}})
        self.assertEqual((r["display"], r["screenshot_b64"]), (2, "bild-2"))
        self.assertNotIn("weitere", r)
        # Danach gilt ohne display Monitor 2 — wie bisher der zuletzt aufgenommene.
        self.assertEqual(self._klick(x=640, y=360), (2400, 540))


if __name__ == "__main__":
    unittest.main()
