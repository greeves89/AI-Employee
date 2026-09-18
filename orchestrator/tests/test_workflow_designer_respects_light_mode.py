"""Der Workflow-Designer folgt dem Erscheinungsbild der App, nicht nur Dunkel.

Nutzeranfrage: „der workflow designer MUSS auch dark und light modes haben."

Zwei Haelften desselben Fehlers:

1. ``<ReactFlow colorMode="dark">`` stand fest verdrahtet — die Zeichenflaeche
   blieb dunkel, egal was der Nutzer im Erscheinungsbild-Umschalter waehlte.
   ``@xyflow/react`` liefert selbst ein helles UND ein dunkles Variablenset;
   es fehlte nur die Weitergabe des tatsaechlichen Themes ueber die
   ``colorMode``-Prop.
2. Die Akzentfarben der Bausteine (blau/bernstein/zink/smaragd/rot/himmelblau)
   nutzten Stufen wie ``-400``/``-300``, die fuer dunklen Grund gewaehlt sind —
   auf hellem Grund sind sie ausgewaschen bis unlesbar. Dasselbe Muster wie in
   ``test_live_output_respects_light_mode.py``, hier auf den Designer
   angewandt: jede solche Stufe braucht eine dunklere Entsprechung ohne
   ``dark:``-Praefix fuer den hellen Grund.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "frontend/src/app/workflows/[id]/page.tsx").read_text()
LIST = (ROOT / "frontend/src/app/workflows/page.tsx").read_text()

ACCENT_COLOURS = "amber|blue|red|violet|emerald|green|yellow|sky|zinc"


class CanvasFollowsTheAppThemeTests(unittest.TestCase):
    def test_color_mode_is_not_hardcoded_to_dark(self):
        self.assertNotIn('colorMode="dark"', EDITOR)
        self.assertNotIn("colorMode='dark'", EDITOR)

    def test_color_mode_is_wired_to_the_theme_hook(self):
        self.assertIn('from "@/components/theme-provider"', EDITOR)
        self.assertIn("useTheme()", EDITOR)
        self.assertIn("colorMode={theme}", EDITOR)


class AccentColoursWorkOnBothGroundsTests(unittest.TestCase):
    """Text-Akzente ohne helle Entsprechung sind auf hellem Grund ausgewaschen —
    dieselbe Ursache wie beim Live-Protokoll, hier in den Designer-Dateien."""

    # Ein Treffer gilt als "hell abgesichert", wenn unmittelbar davor "dark:"
    # steht — mit oder ohne ein dazwischenliegendes "hover:"/"hover:!"
    # (Tailwind stapelt Varianten in dieser Reihenfolge: dark:hover:!text-...).
    _NOT_GUARDED = r"(?<!dark:)(?<!dark:hover:)(?<!dark:hover:!)"

    def test_the_editor_has_no_dark_only_accent_left(self):
        ohne_hell = re.findall(
            rf"{self._NOT_GUARDED}text-(?:{ACCENT_COLOURS})-[34]00", EDITOR
        )
        self.assertEqual(ohne_hell, [], f"ohne helle Entsprechung: {ohne_hell}")

    def test_the_list_page_has_no_dark_only_accent_left(self):
        ohne_hell = re.findall(
            rf"{self._NOT_GUARDED}text-(?:{ACCENT_COLOURS})-[34]00", LIST
        )
        self.assertEqual(ohne_hell, [], f"ohne helle Entsprechung: {ohne_hell}")

    def test_the_dark_appearance_is_unchanged(self):
        """Der dunkle Modus sah richtig aus — er darf sich nicht mitaendern."""
        for accent in ("blue-400", "emerald-400", "red-400", "zinc-300", "sky-400"):
            with self.subTest(accent=accent):
                self.assertIn(f"dark:text-{accent}", EDITOR)


if __name__ == "__main__":
    unittest.main()
