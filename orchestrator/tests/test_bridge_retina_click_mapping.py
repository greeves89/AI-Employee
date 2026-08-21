"""Klicks landen auf Retina-Displays dort, wo das Modell hinzeigt.

Befund 2026-08-18 (Live-Test, Sprachbefehl "oeffne YouTube"): Der Agent
konnte Chrome oeffnen und Screenshots machen, aber seine Klicks landeten
systematisch daneben ("Klicks landen nicht, wo ich sie hinsetze") und er
musste auf Cmd+L/URL ausweichen.

Ursache: Der Screenshot wird auf 1280px Breite herunterskaliert (damit das
Modell keine Koordinaten >1280 halluziniert), aber ein Klick geht an
pyautogui, das in LOGISCHEN Punkten (z.B. 1440) arbeitet. Das Modell nennt
eine Koordinate im 1280er Bild, der Klick landet um den Faktor 1440/1280
daneben.

Der Dispatcher merkt sich jetzt den Maszstab des letzten Screenshots und
rechnet jede Klickkoordinate aus dem Bildraum in den Klickraum zurueck.
find_element liefert seine Treffer im selben Bildraum, damit beide Klickquellen
konsistent sind. Geprueft wird der ECHTE Dispatcher, mit einem Fake-Controller,
der nur die Koordinaten mitschreibt.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "computer-use-bridge"))
import bridge  # noqa: E402


class _RecordingController:
    """Ersetzt den echten InputController und merkt sich nur die Koordinaten."""
    def __init__(self):
        self.calls = []

    def click(self, x, y, button="left", double=False):
        self.calls.append(("click", x, y))

    def scroll(self, x, y, amount):
        self.calls.append(("scroll", x, y))

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def drag(self, x1, y1, x2, y2, duration=0.3):
        self.calls.append(("drag", x1, y1, x2, y2))


class RetinaClickMappingTests(unittest.TestCase):
    def setUp(self):
        self.d = bridge.CommandDispatcher.__new__(bridge.CommandDispatcher)
        self.ctrl = _RecordingController()
        self.d._ctrl = self.ctrl
        self.d.input_recorder = None
        self.d.voice_capture = None
        self.d._browser = None
        self.d._coord_scale = (1.0, 1.0)
        # Seit 1.259.x merkt sich der Dispatcher zusaetzlich den Ursprung des
        # aufgenommenen Bildschirms: bei mehreren Monitoren beginnt der zweite
        # nicht bei 0/0. Ohne Versatz ist es der Hauptbildschirm.
        self.d._coord_offset = (0, 0)

    def _click(self, x, y):
        return self.d.dispatch({"action": "click", "params": {"x": x, "y": y}})

    def test_without_screenshot_coordinates_pass_through(self):
        """Kein Screenshot gelaufen → Maszstab 1.0 → altes Verhalten, 1:1."""
        self._click(200, 300)
        self.assertEqual(self.ctrl.calls[-1], ("click", 200, 300))

    def test_retina_scale_is_applied_to_clicks(self):
        """1280er Bild bei 1440 logischer Breite → Faktor 1.125."""
        # Screenshot-Maszstab setzen, wie es capture_screenshot tut.
        self.d._coord_scale = (1440 / 1280, 900 / 800)  # (1.125, 1.125)
        self._click(640, 400)
        _, cx, cy = self.ctrl.calls[-1]
        self.assertEqual(cx, round(640 * 1.125))  # 720
        self.assertEqual(cy, round(400 * 1.125))  # 450

    def test_scroll_move_drag_are_scaled_too(self):
        self.d._coord_scale = (2.0, 2.0)  # z.B. 5K-Display auf 1280 skaliert
        self.d.dispatch({"action": "scroll", "params": {"x": 100, "y": 100, "amount": 3}})
        self.d.dispatch({"action": "move", "params": {"x": 50, "y": 60}})
        self.d.dispatch({"action": "drag",
                         "params": {"x1": 10, "y1": 20, "x2": 30, "y2": 40}})
        self.assertEqual(self.ctrl.calls[0], ("scroll", 200, 200))
        self.assertEqual(self.ctrl.calls[1], ("move", 100, 120))
        self.assertEqual(self.ctrl.calls[2], ("drag", 20, 40, 60, 80))

    def test_find_element_result_is_in_image_space(self):
        """find_element liefert LOGISCHE AX-Koordinaten; sie muessen in den
        Bildraum umgerechnet werden, sonst skaliert der Klick sie ein zweites
        Mal. Round-trip: image→click muss die logische Koordinate treffen."""
        self.d._coord_scale = (1.125, 1.125)
        logical = {"found": True, "center": {"x": 720, "y": 450},
                   "bbox": {"x": 720, "y": 450, "w": 225, "h": 45}}
        in_image = self.d._element_to_image_space(logical)
        # 720 / 1.125 = 640 (Bildraum)
        self.assertEqual(in_image["center"], {"x": 640, "y": 400})
        # Und ein Klick auf den Bildraum-Punkt trifft wieder die logischen 720.
        self._click(in_image["center"]["x"], in_image["center"]["y"])
        _, cx, cy = self.ctrl.calls[-1]
        self.assertEqual((cx, cy), (720, 450))

    def test_capture_screenshot_reports_scale(self):
        """capture_screenshot MUSS den Maszstab mitliefern, sonst kann der
        Dispatcher ihn nicht setzen. (Kein echter Bildschirm im CI → wir
        pruefen nur die Vertragsform an der Meta-Struktur.)"""
        import inspect
        src = inspect.getsource(bridge.capture_screenshot)
        self.assertIn("scale_x", src)
        self.assertIn("scale_y", src)
        self.assertIn("return b64, meta", src)


if __name__ == "__main__":
    unittest.main()


class SecondScreenOffsetTests(unittest.TestCase):
    """Bei mehreren Monitoren beginnt der zweite NICHT bei 0/0.

    pyautogui klickt ueber alle Bildschirme hinweg in EINEM gemeinsamen Raum.
    Ohne den Versatz landet jeder Klick auf dem zweiten Monitor auf dem ersten —
    genau daneben, und zwar systematisch.
    """

    def setUp(self):
        self.d = bridge.CommandDispatcher.__new__(bridge.CommandDispatcher)
        self.ctrl = _RecordingController()
        self.d._ctrl = self.ctrl
        self.d.input_recorder = None
        self.d.voice_capture = None
        self.d._browser = None
        self.d._coord_scale = (1.0, 1.0)
        # Zweiter Monitor, rechts neben dem ersten.
        self.d._coord_offset = (1920, 0)

    def test_a_click_lands_on_the_second_screen(self):
        self.d.dispatch({"action": "click", "params": {"x": 100, "y": 50}})
        self.assertEqual(self.ctrl.calls[-1], ("click", 2020, 50))

    def test_the_way_back_removes_it_again(self):
        """Beide Richtungen muessen Umkehrungen voneinander bleiben — sonst
        klickt `find_element` daneben."""
        zurueck = self.d._to_image_space(2020, 50)
        self.assertEqual(zurueck, (100, 50))

    def test_scale_and_offset_work_together(self):
        """Ein Retina-Nebenmonitor hat beides: einen Maszstab UND einen
        Versatz."""
        self.d._coord_scale = (2.0, 2.0)
        self.d.dispatch({"action": "click", "params": {"x": 100, "y": 50}})
        self.assertEqual(self.ctrl.calls[-1], ("click", 1920 + 200, 100))
