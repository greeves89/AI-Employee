"""Der Fuellstand des Kontextfensters ueberlebt ein Neuladen.

Nach 1.315.2 zeigte der Ring nach einem Neuladen wieder die Schaetzung, bis
der naechste Zug lief — obwohl sich am Kontext des Agenten durch ein Neuladen
nichts aendert. Rueckmeldung: "nur weil ich eine Seite refreshe, faengt der
doch nicht neu an."

Der Server legt den letzten Aufruf (context_tokens aus dem done-Ereignis) in
der Nachrichten-Meta ab; die Oberflaeche nimmt beim Laden den juengsten Wert.
Ausdruecklich NICHT input_tokens — das ist die Summe aller Aufrufe eines Zuges.
"""

import re
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[1]
FRONT = ORCH.parent / "frontend" / "src" / "components" / "agents" / "chat.tsx"


class ServerSpeichertTests(unittest.TestCase):
    def test_done_meta_enthaelt_context_tokens(self):
        src = (ORCH / "app" / "api" / "ws.py").read_text()
        block = src.split('elif etype == "done":', 1)[1][:3000]
        self.assertIn('"context_tokens": edata["context_tokens"]', block)


class OberflaecheLiestBeimLadenTests(unittest.TestCase):
    def test_letzter_gespeicherter_stand_wird_uebernommen(self):
        src = FRONT.read_text()
        block = src.split("setTaskCards(wiederhergestellt);", 1)[1][:1500]
        self.assertIn("meta?.context_tokens", block)
        self.assertIn("setLiveContextTokens(letzteMitStand?.meta?.context_tokens", block)
        # und NICHT die Summe
        self.assertNotIn("meta?.input_tokens", block)

    def test_der_ring_faellt_nicht_mehr_stumpf_auf_null(self):
        src = FRONT.read_text()
        block = src.split("setTaskCards(wiederhergestellt);", 1)[1][:1500]
        self.assertNotRegex(block, re.compile(r"setLiveContextTokens\(null\)"))


if __name__ == "__main__":
    unittest.main()
