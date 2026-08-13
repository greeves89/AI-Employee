"""Die Protokoll-Ansichten sind im hellen Erscheinungsbild lesbar.

Kundenmeldung vom 2026-08-13: „Im Light Mode beim Task Detail ist die
Hintergrundfarbe des Live Scripts schwarz, passt nicht ganz."

Es war mehr als eine Geschmacksfrage. Der Kasten stand fest auf ``bg-black``,
waehrend die Zeilen darin ``text-foreground`` bzw. ``text-muted-foreground``
benutzen — Farben, die dem Erscheinungsbild folgen. Im hellen Modus ist
``text-foreground`` **dunkel**: dunkle Schrift auf schwarzem Grund. Die Ausgabe
war dort also nicht nur unpassend, sondern stellenweise unlesbar.

Betroffen waren DREI Stellen mit demselben Muster, nicht eine: das Live-Feld der
Aufgabenseite, die Zeitreise-Ansicht darunter (dieselben Zeilen) und der
Live-Terminal des Agenten. Eine davon zu reparieren haette den Fehler nur
verschoben.

Bewusst NICHT angefasst: der Kiosk-Bildschirm (absichtlich schwarz) und der
Hintergrund hinter Bildschirmfotos (dort ist Schwarz die richtige Randfarbe).
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASK = (ROOT / "frontend/src/app/tasks/[id]/page.tsx").read_text()
TERM = (ROOT / "frontend/src/components/terminal/live-terminal.tsx").read_text()


class NoUnconditionalBlackSurfacesTests(unittest.TestCase):
    """``bg-black`` ohne Gegenstueck ist die Ursache — es gewinnt in beiden
    Erscheinungsbildern."""

    def test_the_live_output_panel_follows_the_theme(self):
        self.assertIn("bg-foreground/[0.03] dark:bg-black overflow-hidden", TASK)

    def test_the_time_travel_panel_too(self):
        """Dieselben Zeilen, dasselbe Problem — es haette gereicht, nur das
        obere zu sehen und das untere zu vergessen."""
        self.assertIn(
            "rounded-lg bg-foreground/[0.03] dark:bg-black p-4 font-mono", TASK
        )

    def test_the_agent_terminal_too(self):
        self.assertIn("bg-foreground/[0.03] dark:bg-black flex flex-col h-full", TERM)

    def test_no_bare_black_surface_remains(self):
        """Durchsichtige Ueberlagerungen (``bg-black/40``) sind etwas anderes und
        duerfen bleiben."""
        for name, src in (("tasks", TASK), ("terminal", TERM)):
            with self.subTest(datei=name):
                nackt = re.findall(r"bg-black(?![/\w-])", src)
                begleitet = src.count("dark:bg-black")
                self.assertEqual(len(nackt), begleitet, "bg-black nur mit dark:-Gegenstueck")


class AccentColoursWorkOnBothGroundsTests(unittest.TestCase):
    """Die Stufen -300/-400 sind fuer schwarzen Grund gewaehlt. Auf hellem Grund
    sind sie ausgewaschen — deshalb im Hellen eine dunklere Stufe."""

    def test_every_accent_in_the_log_lines_has_a_light_variant(self):
        """Nur die Zeilen INNERHALB der schwarzen Felder sind gemeint. Auf den
        normalen Karten der Seite ist eine -400-Stufe voellig in Ordnung — dort
        ist der Grund ohnehin hell."""
        rumpf = TASK.split("function TaskLogLine", 1)[1]
        ohne_hell = re.findall(
            r"(?<!dark:)text-(?:amber|blue|red|violet|emerald|green|yellow)-[34]00", rumpf
        )
        self.assertEqual(ohne_hell, [], f"ohne helle Entsprechung: {ohne_hell}")

    def test_the_terminal_has_no_dark_only_accent_left(self):
        ohne_hell = re.findall(
            r"(?<!dark:)text-(?:amber|blue|red|violet|emerald|green|yellow)-[34]00", TERM
        )
        self.assertEqual(ohne_hell, [], f"ohne helle Entsprechung: {ohne_hell}")

    def test_the_dark_appearance_is_unchanged(self):
        """Der dunkle Modus sah richtig aus — er darf sich nicht mitaendern."""
        self.assertIn("dark:text-amber-300", TASK)
        self.assertIn("dark:text-blue-400", TASK)
        self.assertIn("dark:text-emerald-400", TASK)
        self.assertIn("dark:text-green-400", TERM)
        self.assertIn("dark:text-yellow-400", TERM)


if __name__ == "__main__":
    unittest.main()
