"""Nach einer Verdichtung muss die Fuellstandsanzeige den neuen Stand kennen.

Gemeldet: Nach "[Kontext verdichtet: 151k → 65k Token]" blieb der Ring
dauerhaft bei 7 %. Die Meldung war reiner Text — fuer den Menschen lesbar,
fuer die Anzeige nicht. Und die Anzeige rechnete ohnehin nur sichtbaren Text
durch vier, der sich bei einer Verdichtung im Agenten nicht aendert.

Der Agent meldet den neuen Stand jetzt zusaetzlich als Zahl ("context"-Ereignis).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm_chat_handler import LLMChatHandler


def _handler(publisher):
    h = LLMChatHandler.__new__(LLMChatHandler)
    h.log_publisher = publisher
    h._history = []
    h._last_input_tokens = 0
    h._compaction_floor = 0
    h._overhead_tokens = 0
    return h


class VerdichtungMeldetZahlTests(unittest.TestCase):
    def test_das_ereignis_steht_im_quelltext_neben_dem_text(self):
        """Verhaltensnah, ohne die ganze Kompaktierung nachzubauen: Das
        Zahl-Ereignis muss dort abgesetzt werden, wo auch der Text rausgeht."""
        quelle = (Path(__file__).resolve().parents[1] / "app" / "llm_chat_handler.py").read_text()
        block = quelle.split("[Kontext verdichtet:", 1)[1][:900]
        self.assertIn('"context"', block)
        self.assertIn('"tokens": after', block)



class DoneMeldetDenLetztenAufrufTests(unittest.TestCase):
    """input_tokens im done ist die SUMME aller Aufrufe eines Zuges — bei fuenf
    Werkzeug-Runden das Fuenffache des Fensters. Der Fuellstand braucht den
    letzten Aufruf, als eigenes Feld."""

    def test_context_tokens_kommt_aus_dem_letzten_aufruf(self):
        quelle = (Path(__file__).resolve().parents[1] / "app" / "llm_chat_handler.py").read_text()
        block = quelle.split('"status": "completed",', 1)[1][:1200]
        self.assertIn('"context_tokens": self._last_input_tokens', block)
        # und NICHT die Summe unter diesem Namen
        self.assertNotIn('"context_tokens": total_input_tokens', block)


if __name__ == "__main__":
    unittest.main()
