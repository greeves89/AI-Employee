"""Jede Themenfarbe, die die Oberfläche benutzt, ist im Design auch definiert.

Tailwind erzeugt für eine unbekannte Farbe keine Regel und meldet nichts: Das Fenster
„Subagenten dieser Sitzung“ nutzte ``bg-popover``, die Farbe gab es nicht — der
Hintergrund blieb durchsichtig und der Chat schien durch (Meldung vom 06.10.2026).
"""

import re
import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
#: Namen der Themenfarben (shadcn-Schema), die als bg-/text-/border-Klasse vorkommen können.
THEMENFARBEN = ("background", "foreground", "card", "card-foreground", "popover",
                "popover-foreground", "primary", "primary-foreground", "secondary",
                "secondary-foreground", "muted", "muted-foreground", "accent",
                "accent-foreground", "destructive", "destructive-foreground", "border",
                "input", "ring", "success", "warning", "info")


class ThemenfarbenTests(unittest.TestCase):
    def test_benutzte_themenfarben_sind_definiert(self):
        css = (FRONTEND / "app" / "globals.css").read_text(encoding="utf-8")
        definiert = set(re.findall(r"--color-([a-z-]+)\s*:", css))
        muster = re.compile(
            r"(?<![\w-])(?:bg|text|border|ring|fill|stroke|from|to|via|outline|divide|placeholder)-("
            + "|".join(sorted(THEMENFARBEN, key=len, reverse=True)) + r")(?![\w-])")
        benutzt: dict[str, str] = {}
        for datei in FRONTEND.rglob("*.tsx"):
            for name in muster.findall(datei.read_text(encoding="utf-8")):
                benutzt.setdefault(name, str(datei.relative_to(FRONTEND)))
        fehlend = {n: wo for n, wo in benutzt.items() if n not in definiert}
        self.assertEqual(fehlend, {}, f"Benutzt, aber in globals.css nicht definiert: {fehlend}")


if __name__ == "__main__":
    unittest.main()
