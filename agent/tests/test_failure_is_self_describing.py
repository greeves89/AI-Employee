"""Ein Fehler muss erklaeren, was passiert ist — sonst kostet er eine Stunde.

Kundenfall bei einem Kunden, 2026-08-13: Drei Aufgaben scheiterten mit

    Unexpected error: ReadError('')

Die Klammer ist **leer**. Kein Status, keine Groesse, kein Endpunkt, kein Modell,
keine Laufzeit — und vor allem nicht der Fehler, der wirklich passiert ist:
``httpx`` verpackt den Socket-Abbruch, und ``ReadError`` ist nur die Huelle.
Darunter steht ``ConnectionResetError``, ``SSLEOFError``, ``EndOfStream`` oder
``IncompleteRead``, je nachdem, wer die Verbindung gekappt hat.

Um ueberhaupt eingrenzen zu koennen, WO es reisst, musste ich 110 gespeicherte
Aufgabenschritte auf der Kundenanlage durchsehen. Das Ergebnis passt in eine
Zeile — sie wurde nur nie geschrieben.

Bewusst OHNE Inhalte: Groessen, Anzahl und Host ja, Prompt nein. Die Meldung
landet in der Oberflaeche und in Protokollen.
"""

import time
import unittest

from app.providers.base import describe_failure, format_exception


def _mit_ursache() -> Exception:
    """Ein ``ReadError('')`` mit dem echten Grund darunter — wie httpx ihn wirft."""
    try:
        try:
            raise ConnectionResetError(104, "Connection reset by peer")
        except ConnectionResetError as innen:
            raise _ReadError("") from innen
    except Exception as aussen:  # noqa: BLE001
        return aussen


class _ReadError(Exception):
    """Steht hier fuer ``httpx.ReadError`` — leerer Text, echter Grund darunter."""


class TheCauseChainSurvivesTests(unittest.TestCase):
    """Der Kern. Ohne das ist die Meldung buchstaeblich leer."""

    def test_the_real_reason_is_included(self):
        text = format_exception(_mit_ursache())
        self.assertIn("ConnectionResetError", text)
        self.assertIn("Connection reset by peer", text)

    def test_the_outer_shell_is_still_named(self):
        """Beides gehoert hin: wo es aufschlug UND warum."""
        self.assertIn("_ReadError", format_exception(_mit_ursache()))

    def test_a_plain_exception_is_unchanged(self):
        self.assertEqual(format_exception(ValueError("kaputt")), "ValueError: kaputt")

    def test_an_empty_exception_never_returns_nothing(self):
        self.assertTrue(format_exception(_ReadError("")).strip())

    def test_a_cycle_does_not_hang(self):
        """``__context__`` kann im Kreis zeigen — das darf nicht haengen."""
        a = ValueError("a")
        b = ValueError("b")
        a.__context__ = b
        b.__context__ = a
        self.assertTrue(format_exception(a))


class TheCircumstancesAreRecordedTests(unittest.TestCase):
    def test_it_names_model_and_size(self):
        text = describe_failure(
            _mit_ursache(),
            url="https://example.openai.azure.com/openai/deployments/gpt-5/chat/completions?api-version=2024",
            body={"messages": [{"role": "user", "content": "x" * 500}]},
            messages=[1, 2, 3],
            model="gpt-5",
            started=time.monotonic() - 2.0,
        )
        self.assertIn("Modell=gpt-5", text)
        self.assertIn("Nachrichten=3", text)
        self.assertIn("Anfrage=", text)
        self.assertIn("nach 2.0s", text)

    def test_the_query_string_is_dropped(self):
        """Gemini traegt den Schluessel in der Abfrage (``?key=…``) — der darf
        NIE in eine Fehlermeldung, die in Protokollen landet."""
        text = describe_failure(
            _mit_ursache(),
            url="https://generativelanguage.googleapis.com/v1/models/gemini:stream?key=GEHEIM123&alt=sse",
        )
        self.assertNotIn("GEHEIM123", text)
        self.assertNotIn("key=", text)
        self.assertIn("generativelanguage.googleapis.com", text)

    def test_the_prompt_itself_is_never_included(self):
        text = describe_failure(
            _mit_ursache(),
            body={"messages": [{"role": "user", "content": "PATIENTENDATEN"}]},
        )
        self.assertNotIn("PATIENTENDATEN", text)

    def test_it_works_with_nothing_to_say(self):
        self.assertTrue(describe_failure(ValueError("x")).strip())

    def test_a_body_that_cannot_be_measured_does_not_break_it(self):
        """Eine Diagnose, die selbst scheitert, verschluckt den Befund."""
        self.assertIn("ValueError", describe_failure(ValueError("x"), body={"o": object()}))


class EveryProviderUsesItTests(unittest.TestCase):
    """Eine Stelle zu vergessen heisst, genau dort wieder blind zu sein."""

    import pathlib

    ROOT = pathlib.Path(__file__).resolve().parents[2] / "agent/app/providers"

    def test_no_error_site_reports_bare_anymore(self):
        for name in ("openai_provider.py", "anthropic_provider.py", "google_provider.py"):
            src = (self.ROOT / name).read_text()
            with self.subTest(provider=name):
                self.assertNotIn('text=f"Connection failed: {e}"', src)
                self.assertNotIn('text="Request timed out"', src)
                self.assertNotIn('text=f"Unexpected error: {format_exception(e)}"', src)
                self.assertIn("_diag(e)", src)

    def test_each_provider_builds_the_context(self):
        for name in ("openai_provider.py", "anthropic_provider.py", "google_provider.py"):
            src = (self.ROOT / name).read_text()
            with self.subTest(provider=name):
                self.assertIn("describe_failure(", src)
                self.assertIn("started=_start", src)


if __name__ == "__main__":
    unittest.main()
