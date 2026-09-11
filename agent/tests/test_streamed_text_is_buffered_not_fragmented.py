"""Ein Auftrag wurde zu 246 Ein-Wort-Schritten statt einer Antwort (beim Kunden, 2026-09-11).

Der Custom-LLM-Runner (GPT-Modelle) verarbeitet einen rohen Token-Stream vom
Provider. Fuer jedes einzelne ``text_delta``-Fragment ging bisher SOFORT ein
eigenes ``"text"``-Ereignis auf ``agents:logs:all`` raus, und der Orchestrator
schreibt davon EINE Zeile pro Ereignis in die ``TaskStep``-Tabelle — dieselbe
Tabelle, die sowohl die Task-Karten-Aktivitaetsanzeige im Chat als auch die
Replay-Ansicht speist. Eine einzelne Wetterauskunft wurde so zu "Be", "ob",
"acht", "ungs", "zeit", "punkt", ":**" ... — 246 unlesbare Zeilen statt einem
zusammenhaengenden Antworttext.

Claude Code und Codex haben dieses Problem nicht: sie liefern bereits
vollstaendige Textbloecke pro Ereignis (siehe ``agent_runner.py``/
``codex_runner.py``), keine rohen Provider-Token. Der Fix puffert die Deltas
im Custom-LLM-Runner deshalb bis zum naechsten natuerlichen Bruchpunkt
(Werkzeugaufruf, Fehler/Wiederholung, oder Ende des Streams) und veroeffentlicht
dann EIN Ereignis mit dem vollstaendigen Text — angeglichen an das Verhalten
der anderen beiden Laufzeiten.

Ein voller asynchroner Lauf durch ``LLMRunner.run_task`` waere hier ein großer,
bruechiger Mock-Aufbau (Provider, Tools, DB, Settings) ohne bestehende
Test-Infrastruktur dafuer — deshalb wie bei den bestehenden Kontrollfluss-Tests
in ``test_connection_glitch_is_retried.py`` eine Quelltextpruefung: das
Streaming-Ereignis wird NICHT mehr direkt veroeffentlicht, und der Puffer wird
an jedem der drei Bruchpunkte geleert.
"""

import pathlib
import re
import unittest

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "app" / "llm_runner.py").read_text()


def _turn_loop_body() -> str:
    """Der Abschnitt vom Schleifenstart bis kurz vor 'switched_model' danach —
    genau der Bereich, in dem ein Zug gestreamt und geflusht wird."""
    start = _SRC.index("while num_turns < max_turns:")
    end = _SRC.index("if switched_model:", start)
    return _SRC[start:end]


class TheRawDeltaIsNoLongerPublishedDirectlyTests(unittest.TestCase):
    def test_text_delta_does_not_publish_immediately(self):
        """Der alte Bug in einem Satz: jedes Fragment ging sofort raus."""
        body = _turn_loop_body()
        delta_block = body.split('event.type == "text_delta"', 1)[1].split("elif event.type ==", 1)[0]
        self.assertNotIn('"text", {"text": event.text}', delta_block)

    def test_deltas_accumulate_into_a_buffer(self):
        body = _turn_loop_body()
        delta_block = body.split('event.type == "text_delta"', 1)[1].split("elif event.type ==", 1)[0]
        self.assertIn("pending_text += event.text", delta_block)


class ThePufferIsFlushedAtEveryBreakPointTests(unittest.TestCase):
    """Ohne Flush an JEDEM Bruchpunkt geht entweder Text verloren, oder er
    landet in der falschen Reihenfolge relativ zum naechsten Werkzeugaufruf."""

    def test_flushed_before_a_tool_call_is_published(self):
        body = _turn_loop_body()
        tool_call_block = body.split('event.type == "tool_call"', 1)[1].split("elif event.type ==", 1)[0]
        flush_pos = tool_call_block.find("if pending_text:")
        publish_pos = tool_call_block.find('"tool_call",')
        self.assertNotEqual(flush_pos, -1, "kein Flush vor dem tool_call-Zweig")
        self.assertLess(flush_pos, publish_pos, "Flush muss VOR der tool_call-Veroeffentlichung stehen")

    def test_flushed_before_the_connection_retry_and_fallback_switch(self):
        body = _turn_loop_body()
        error_block = body.split('event.type == "error"', 1)[1]
        flush_pos = error_block.find("if pending_text:")
        retry_pos = error_block.find("_retry_after_connection_glitch")
        self.assertNotEqual(flush_pos, -1, "kein Flush im error-Zweig")
        self.assertLess(flush_pos, retry_pos, "Flush muss VOR dem Wiederholungsversuch stehen")

    def test_flushed_after_the_stream_ends_before_the_retry_check(self):
        """Der normale Fall: der Zug ist zu Ende, kein Werkzeugaufruf mehr
        danach — ohne diesen Flush geht der komplette Antworttext verloren."""
        body = _turn_loop_body()
        # Ausserhalb der async-for-Schleife, aber noch vor 'switched_model' geprueft.
        after_loop = body[body.rindex("async for event"):]
        pending_count = after_loop.count("if pending_text:")
        self.assertGreaterEqual(pending_count, 2, "Flush fehlt fuer den Normalfall (Streamende)")


class BufferIsIntroducedWithAComment(unittest.TestCase):
    def test_the_buffer_is_declared_per_turn(self):
        """Ein Puffer, der Zuege ueberlebt, wuerde Text aus einem frueheren Zug
        an einen spaeteren anhaengen."""
        body = _turn_loop_body()
        decl_pos = body.find("pending_text = \"\"")
        stream_pos = body.find("async for event")
        self.assertNotEqual(decl_pos, -1)
        self.assertLess(decl_pos, stream_pos, "Puffer muss vor dem Stream-Start je Zug leer beginnen")


if __name__ == "__main__":
    unittest.main()
