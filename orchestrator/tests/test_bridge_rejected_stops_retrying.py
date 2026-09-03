"""Eine endgueltige Server-Ablehnung (1008) beendet die Verbindungsschleife.

Befund 2026-08-18: Der 1008-Zweig meldete zwar "rejected" an die Oberflaeche,
lief dann aber in dieselbe 5-Sekunden-Schleife wie ein Netzwerkwackler — die
Bridge waehlte eine abgelaufene Session bis in alle Ewigkeit neu an, und das
Tray-Symbol stand auf "verbunden", weil der Thread ja lebte. Fuer den Nutzer
sah eine tote Session damit exakt so aus wie eine gesunde Verbindung.

1008 heisst: Session abgelaufen/unbekannt, falscher Nutzer, oder eine andere
Bridge haengt bereits dran. Kein Wiederholen der Welt aendert daran etwas —
die einzige richtige Reaktion ist aufhoeren und den Menschen informieren.
"""

import re
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[2] / "computer-use-bridge/bridge.py"


class RejectedStopsRetryingTests(unittest.TestCase):
    def test_1008_branch_returns_instead_of_sleeping(self):
        src = BRIDGE.read_text(encoding="utf-8")
        match = re.search(r'code", None\) == 1008:\n(?P<body>.*?)\n\s*return\b',
                          src, re.DOTALL)
        self.assertIsNotNone(
            match,
            "Der 1008-Zweig endet nicht mit return — nach einer endgueltigen "
            "Ablehnung muss die Schleife enden, sonst waehlt die Bridge eine "
            "tote Session ewig neu an",
        )
        body = match.group("body")
        self.assertIn('self._emit_state("rejected"', body,
                      "Die Oberflaeche erfaehrt nichts von der Ablehnung")
        self.assertNotIn("sleep", body,
                         "Der 1008-Zweig darf nicht in die Warten-und-nochmal-Schleife laufen")


if __name__ == "__main__":
    unittest.main()
