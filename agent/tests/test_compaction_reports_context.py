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

_QUELLE = (Path(__file__).resolve().parents[1] / "app" / "llm_chat_handler.py").read_text()


def _block(anker: str, ende: str) -> str:
    """Der Ausschnitt zwischen zwei Marken des Quelltextes.

    Absichtlich an einer STRUKTUR-Marke begrenzt und nicht an einer Zeichenzahl:
    ein festes Fenster laeuft ueber, sobald zwischen Anker und Nadel etwas
    dazukommt, und der Test wird rot, ohne dass die Sache kaputt waere — genau
    so ist die Hauptlinie stehengeblieben, als dem Ergebnis Felder hinzukamen.
    """
    teile = _QUELLE.split(anker, 1)
    if len(teile) != 2:
        raise AssertionError("Anker nicht mehr im Quelltext: " + anker)
    rest = teile[1]
    schluss = rest.find(ende)
    if schluss < 0:
        raise AssertionError("Ende-Marke nicht nach dem Anker: " + ende)
    return rest[:schluss]


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
        block = _block("[Kontext verdichtet:", "\n        else:")
        self.assertIn('"context"', block)
        self.assertIn('"tokens": after', block)



class DoneMeldetDenLetztenAufrufTests(unittest.TestCase):
    """input_tokens im done ist die SUMME aller Aufrufe eines Zuges — bei fuenf
    Werkzeug-Runden das Fuenffache des Fensters. Der Fuellstand braucht den
    letzten Aufruf, als eigenes Feld."""

    def test_context_tokens_kommt_aus_dem_letzten_aufruf(self):
        block = _block('"status": "completed",', 'publish_chat(message_id, "done"')
        self.assertIn('"context_tokens": self._last_input_tokens', block)
        # und NICHT die Summe unter diesem Namen
        self.assertNotIn('"context_tokens": total_input_tokens', block)


if __name__ == "__main__":
    unittest.main()
