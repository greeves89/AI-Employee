"""Nach einer Verdichtung muss die Fuellstandsanzeige den neuen Stand kennen.

Gemeldet: Nach "[Kontext verdichtet: 151k → 65k Token]" blieb der Ring
dauerhaft bei 7 %. Die Meldung war reiner Text — fuer den Menschen lesbar,
fuer die Anzeige nicht. Und die Anzeige rechnete ohnehin nur sichtbaren Text
durch vier, der sich bei einer Verdichtung im Agenten nicht aendert.

Der Agent meldet den neuen Stand jetzt zusaetzlich als Zahl ("context"-Ereignis).

Geprueft wird der ECHTE Ablauf mit Doppeln fuer Modell und Ausgabe. Vorher
stand hier eine Quelltext-Suche in einem festen Zeichenfenster; die wurde rot,
sobald dazwischen etwas dazukam, und ging still durch, wenn die Marke
verrutschte.
"""

import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm_chat_handler  # noqa: E402
from app.llm_chat_handler import LLMChatHandler  # noqa: E402


class _Mitschrift:
    def __init__(self):
        self.ereignisse: list[tuple[str, dict]] = []

    async def publish_chat(self, message_id, typ, daten=None):
        self.ereignisse.append((typ, daten or {}))

    def vom_typ(self, typ):
        return [d for t, d in self.ereignisse if t == typ]


def _handler(publisher):
    h = LLMChatHandler.__new__(LLMChatHandler)
    h.log_publisher = publisher
    h._history = [llm_chat_handler.ChatMessage(role="system", content="x")]
    h._last_input_tokens = 0
    h._compaction_floor = 0
    h._overhead_tokens = 0
    return h


class VerdichtungMeldetZahlTests(unittest.IsolatedAsyncioTestCase):
    async def _verdichte(self, staende):
        """Faehrt _compact_history mit vorgegebenen Fuellstaenden durch.

        ``staende`` sind die Werte, die _estimate_tokens nacheinander liefert:
        vorher, Zwischenstand (entscheidet ueber Schicht 4), nachher.
        """
        h = _handler(_Mitschrift())
        h._get_provider = lambda: object()
        h._get_context_window = lambda: 200_000
        h._estimate_tokens = unittest.mock.Mock(side_effect=list(staende))
        with unittest.mock.patch.object(
            llm_chat_handler.context_compressor, "compaction_target_tokens",
            return_value=1_000,
        ), unittest.mock.patch.object(
            llm_chat_handler.context_compressor, "compress_messages",
            return_value=(h._history, []),
        ):
            await h._compact_history("m1")
        return h

    async def test_die_zahl_geht_mit_dem_text_zusammen_raus(self):
        """Der Text ist fuer den Menschen, die Zahl fuer die Anzeige. Fehlt die
        Zahl, bleibt der Ring stehen — genau der gemeldete Fehler."""
        h = await self._verdichte([151_000, 900, 65_000])
        self.assertEqual(h.log_publisher.vom_typ("context"),
                         [{"tokens": 65_000, "before": 151_000}])
        text = h.log_publisher.vom_typ("text")
        self.assertTrue(text and "Kontext verdichtet" in text[0]["text"])

    async def test_ohne_wirkung_wird_nichts_gemeldet(self):
        """Eine Meldung ohne Verdichtung war die sichtbare Haelfte der
        Beschwerde "komprimiert dauernd" — Banner ohne Arbeit."""
        h = await self._verdichte([65_000, 900, 65_000])
        self.assertEqual(h.log_publisher.ereignisse, [])


class DoneMeldetDenLetztenAufrufTests(unittest.IsolatedAsyncioTestCase):
    """input_tokens im done ist die SUMME aller Aufrufe eines Zuges — bei fuenf
    Werkzeug-Runden das Fuenffache des Fensters. Der Fuellstand braucht den
    letzten Aufruf, als eigenes Feld."""

    async def test_context_tokens_kommt_aus_dem_letzten_aufruf(self):
        h = _handler(_Mitschrift())
        h.is_running = False
        h._stopping = False
        h._loop_detector = SimpleNamespace(reset=lambda: None,
                                           record=lambda *a: None,
                                           is_looping=lambda: False)
        h._connection_retries = 0
        h._models_tried = set()
        h.pending_drain = None
        h._get_tools = unittest.mock.AsyncMock(return_value=[])
        h._needs_compaction = lambda: False
        h._tool_executor = SimpleNamespace(execute=unittest.mock.AsyncMock(return_value="ok"))

        # Zwei Zuege: erst ein Werkzeug (40 Token Fenster), dann die Antwort
        # (60 Token Fenster). Summe 100, Fenster 60 — die Zahlen sind absichtlich
        # verschieden, sonst belegt der Test nichts.
        zuege = [
            [SimpleNamespace(type="tool_call", tool_id="t1", tool_name="read_file",
                             tool_input={"path": "/tmp/x"}),
             SimpleNamespace(type="done", input_tokens=40, output_tokens=1)],
            [SimpleNamespace(type="text_delta", text="fertig"),
             SimpleNamespace(type="done", input_tokens=60, output_tokens=2)],
        ]

        class _Modell:
            reasoning_effort = None

            async def stream_completion(self, verlauf, werkzeuge):
                for e in zuege.pop(0):
                    yield e

        h._get_provider = lambda: _Modell()
        with unittest.mock.patch.object(
            llm_chat_handler, "report_result_status", unittest.mock.AsyncMock()
        ):
            result = await h.handle_message("m1", "hallo")

        self.assertEqual(result["input_tokens"], 100, "die Kosten brauchen die Summe")
        self.assertEqual(result["context_tokens"], 60, "der Fuellstand den letzten Aufruf")


if __name__ == "__main__":
    unittest.main()
