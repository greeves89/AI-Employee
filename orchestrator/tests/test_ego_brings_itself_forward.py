"""ego lite lief nach einem erfolgreichen ego_run im HINTERGRUND weiter.

Live gemeldet (27.08.2026): die Automatisierung funktionierte nachweislich
(echte Tabs, echte Suchergebnisse — per System-Events-Fensterliste bestaetigt),
aber ``ego-browser`` startet die App im Hintergrund
(``--startup-ego-browser-service``, kein sichtbares Fenster vorne). Der Nutzer
sah nichts und hielt es fuer kaputt. ``open -a "ego lite"`` danach holt sie
sichtbar nach vorn — hier live verifiziert (frontmost wechselte tatsaechlich).
Dieser Test haelt NUR die Verdrahtung fest (Quelltext-Ebene, wie die anderen
Bridge-Tests — ein echter Fensterwechsel laesst sich in CI nicht pruefen).
"""

import re
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2] / "computer-use-bridge/bridge.py"


def _source() -> str:
    return BRIDGE.read_text(encoding="utf-8")


class EgoForegroundTests(unittest.TestCase):
    def setUp(self):
        self.src = _source()

    def test_bring_to_front_helper_exists_and_is_mac_gated(self):
        self.assertIn("def _ego_bring_to_front(", self.src)
        block = self.src.split("def _ego_bring_to_front(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if not IS_MAC:", block)
        self.assertIn('"open", "-a", "ego lite"', block)

    def test_a_failed_activation_never_raises(self):
        """Kosmetik darf das eigentliche Ergebnis nicht kaputt machen."""
        block = self.src.split("def _ego_bring_to_front(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("except Exception", block)

    def test_ego_run_calls_it_only_on_success(self):
        ego_run_body = self.src.split("def ego_run(", 1)[1].split("\ndef ", 1)[0]
        before_return = ego_run_body.split('return {"ok": True, "output": output}', 1)[0]
        self.assertIn("_ego_bring_to_front()", before_return)
        # NICHT auf jedem Fehlerpfad — sonst poppt die App bei jedem
        # kaputten Skript unnoetig auf.
        for fehlerpfad in re.findall(r'return \{"ok": False.*?\}', ego_run_body):
            self.assertNotIn("_ego_bring_to_front", fehlerpfad)


if __name__ == "__main__":
    unittest.main()
