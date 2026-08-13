"""Eine abgerissene Verbindung tötet nicht die ganze Aufgabe.

Kundenfall vom 2026-08-13: Drei Aufgaben scheiterten an ``ReadError('')``. Der
Abbruch traf jedes Mal das Lesen der Modell-Antwort, kurz nachdem ein groesserer
Stapel Werkzeug-Ergebnisse zurueckging. Eine Aufgabe, die vierzig Zuege gelaufen
war, starb an einem einzigen abgerissenen Lesevorgang.

Zwei Luecken lagen uebereinander:

1. ``is_retryable`` kannte nur „das Modell kann gerade nicht" (Rate-Limit, 5xx,
   Ueberlastung). Ein Socket-Abbruch passte auf keinen Marker und galt deshalb
   als endgueltig.
2. Selbst mit Treffer haette es nichts genutzt: die Wiederholung wechselt das
   MODELL, und die Ausweichkette ist im Regelfall leer — ``next_model`` haette
   ``None`` geliefert.

Deshalb ist der Verbindungsabbruch eine eigene Kategorie: nicht Modell wechseln,
sondern **denselben Aufruf noch einmal**. Das Modell war in Ordnung, die Leitung
war es nicht.
"""

import unittest

from app import model_fallback as mf


class ConnectionGlitchesAreRecognisedTests(unittest.TestCase):
    def test_the_customers_actual_error(self):
        """Genau der Text, der dreimal eine Aufgabe gekostet hat."""
        self.assertTrue(mf.is_connection_glitch("Unexpected error: ReadError('')"))

    def test_the_underlying_cause_counts_too(self):
        """Seit der Diagnose liefert die Meldung die Ursachenkette mit."""
        self.assertTrue(mf.is_connection_glitch(
            "ReadError <- ConnectionResetError: Connection reset by peer"))

    def test_common_variants(self):
        for text in (
            "httpx.ConnectError: connection refused",
            "RemoteDisconnected: Server disconnected without response",
            "anyio.EndOfStream",
            "http.client.IncompleteRead(0 bytes read)",
            "ssl.SSLEOFError: EOF occurred in violation of protocol",
            "Connection aborted",
        ):
            with self.subTest(text=text):
                self.assertTrue(mf.is_connection_glitch(text))


class ItStaysDistinctFromTheOtherCasesTests(unittest.TestCase):
    """Die Unterscheidung ist der ganze Punkt — sonst wechselt man Modelle,
    wenn nur die Leitung zuckte, oder wiederholt bei einem kaputten Schluessel."""

    def test_a_capacity_problem_is_not_a_connection_glitch(self):
        self.assertFalse(mf.is_connection_glitch("429 rate limit exceeded"))
        self.assertTrue(mf.is_retryable("429 rate limit exceeded"))

    def test_a_setup_error_is_neither(self):
        """Ein falscher Schluessel wird durch Wiederholen nicht richtiger."""
        for text in ("401 Unauthorized", "DeploymentNotFound",
                     "invalid_api_key — connection closed"):
            with self.subTest(text=text):
                self.assertFalse(mf.is_connection_glitch(text))
                self.assertFalse(mf.is_retryable(text))

    def test_an_empty_message_is_not_an_invitation(self):
        self.assertFalse(mf.is_connection_glitch(""))
        self.assertFalse(mf.is_connection_glitch(None))

    def test_an_unknown_error_is_not_retried(self):
        self.assertFalse(mf.is_connection_glitch("Something went sideways"))


class BothRuntimesRetryTheSameWayTests(unittest.TestCase):
    """Harness-Paritaet: haette nur der Auftrag die Wiederholung, waere der Chat
    ohne Grund schlechter dran."""

    import pathlib

    ROOT = pathlib.Path(__file__).resolve().parents[2] / "agent/app"

    def _quelle(self, name: str) -> str:
        return (self.ROOT / name).read_text()

    def test_both_have_the_helper(self):
        for name in ("llm_runner.py", "llm_chat_handler.py"):
            with self.subTest(datei=name):
                self.assertIn("async def _retry_after_connection_glitch", self._quelle(name))

    def test_it_is_tried_before_switching_the_model(self):
        """Erst der billige Fall. Andersherum wuerde ein Leitungszucken einen
        Modellwechsel ausloesen — teurer und irrefuehrend."""
        for name in ("llm_runner.py", "llm_chat_handler.py"):
            src = self._quelle(name)
            with self.subTest(datei=name):
                self.assertLess(
                    src.index("_retry_after_connection_glitch(t" if "runner" in name
                              else "_retry_after_connection_glitch(m"),
                    src.index("_switch_to_fallback(t" if "runner" in name
                              else "_switch_to_fallback(m"),
                )

    def test_the_turn_is_actually_repeated(self):
        """Der Merker entscheidet, ob die Schleife den Zug neu anfaengt. Ohne ihn
        waere die „Wiederholung" ein stiller Abbruch mit halber Antwort."""
        for name in ("llm_runner.py", "llm_chat_handler.py"):
            src = self._quelle(name)
            # Ab der AUFRUFSTELLE, nicht ab der Methodendefinition.
            aufruf = ("_retry_after_connection_glitch(task_id, event.text)"
                      if "runner" in name
                      else "_retry_after_connection_glitch(message_id, event.text)")
            block = src.split(aufruf, 1)[1][:400]
            with self.subTest(datei=name):
                self.assertIn("switched_model = True", block)

    def test_it_is_bounded(self):
        """Reisst es dreimal, liegt es nicht am Zufall — dann muss der echte
        Grund sichtbar werden statt still weiterprobiert."""
        for name in ("llm_runner.py", "llm_chat_handler.py"):
            with self.subTest(datei=name):
                self.assertIn("self._connection_retries >= 2", self._quelle(name))

    def test_the_counter_starts_at_zero(self):
        for name in ("llm_runner.py", "llm_chat_handler.py"):
            with self.subTest(datei=name):
                self.assertIn("self._connection_retries: int = 0", self._quelle(name))

    def test_the_human_is_told(self):
        """Ein stilles Wiederholen verschleiert, warum ein Lauf laenger dauert."""
        for name in ("llm_runner.py", "llm_chat_handler.py"):
            with self.subTest(datei=name):
                self.assertIn("Verbindung abgerissen — neuer Versuch", self._quelle(name))


if __name__ == "__main__":
    unittest.main()
