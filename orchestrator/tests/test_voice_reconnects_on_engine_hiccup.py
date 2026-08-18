"""Ein Schluckauf der Sprach-Engine darf das Gespraech nicht beenden.

Nutzerbericht vom 18.08.2026: „Model has timed out in processing the request.
Try your request again." — und danach stand das Live-Gespraech. Der Browser
zeigte den Fehler an und blieb stehen; neu starten musste der Nutzer von Hand.

Dabei gibt es das Neuverbinden laengst: reisst der Stream ab (der bekannte
AWS-CRT-Fall), verbindet die Oberflaeche still neu und setzt DASSELBE Gespraech
fort. Nur ein Fehler der Engine lief in einen anderen Zweig — obwohl es
dasselbe ist. „Try your request again" steht sogar in der Meldung.

**Warum eine Positivliste und keine Faustregel.** Was hier nicht steht, wird dem
Nutzer gezeigt. Ein falscher Zugangsschluessel wuerde sonst achtmal hintereinander
scheitern, ohne dass jemand erfaehrt, warum — und am Ende stuende dieselbe
Meldung, nur acht Versuche spaeter.
"""

import unittest
from pathlib import Path

from app.services.realtime_voice_session import _ist_voruebergehend

ROOT = Path(__file__).resolve().parents[2]
UI = (ROOT / "frontend/src/components/agents/voice-session.tsx").read_text()
VOICE = (ROOT / "orchestrator/app/services/realtime_voice_session.py").read_text()


class TransientErrorsAreRecognisedTests(unittest.TestCase):
    #: Woertlich die Meldung aus dem Bericht.
    GEMELDET = "Model has timed out in processing the request. Try your request again."

    def test_the_reported_message(self):
        self.assertTrue(_ist_voruebergehend(self.GEMELDET))

    def test_other_hiccups_too(self):
        for m in ("ThrottlingException: Rate exceeded",
                  "Service Unavailable",
                  "Internal Server Error",
                  "Connection reset by peer",
                  "AWS_ERROR_HTTP_STREAM_HAS_COMPLETED"):
            with self.subTest(meldung=m):
                self.assertTrue(_ist_voruebergehend(m))

    def test_case_does_not_matter(self):
        self.assertTrue(_ist_voruebergehend("MODEL HAS TIMED OUT"))

    def test_empty_is_not_transient(self):
        """Ohne Meldung wissen wir nichts — dann lieber zeigen als im Kreis
        verbinden."""
        self.assertFalse(_ist_voruebergehend(""))
        self.assertFalse(_ist_voruebergehend(None))


class RealProblemsStayVisibleTests(unittest.TestCase):
    """Still neu zu verbinden waere hier das Schlimmste: der Nutzer wartet, und
    niemand sagt ihm, dass der Zugang fehlt."""

    def test_bad_credentials_are_shown(self):
        for m in ("The security token included in the request is invalid",
                  "AccessDeniedException: not authorized to invoke this model",
                  "UnrecognizedClientException"):
            with self.subTest(meldung=m):
                self.assertFalse(_ist_voruebergehend(m))

    def test_a_missing_model_is_shown(self):
        self.assertFalse(_ist_voruebergehend("ValidationException: model not found"))


class TheBrowserActuallyReconnectsTests(unittest.TestCase):
    def test_the_server_marks_the_error(self):
        self.assertIn('"retryable": _ist_voruebergehend(meldung)', VOICE)

    def test_the_browser_reads_the_mark(self):
        self.assertIn("data.retryable", UI)

    def test_it_goes_the_same_way_as_a_dropped_stream(self):
        """Nicht ein zweiter Weg daneben — derselbe."""
        block = UI.split("data.retryable", 1)[1][:400]
        self.assertIn('setState("connecting")', block)

    def test_the_retry_limit_still_applies(self):
        """Ein Dauerfehler darf nicht still im Kreis laufen."""
        block = UI.split("data.retryable", 1)[1][:400]
        self.assertIn("MAX_VOICE_RECONNECTS", block)

    def test_after_the_limit_the_error_is_shown(self):
        block = UI.split("data.retryable", 1)[1][:600]
        self.assertIn("setError(", block)

    def test_the_conversation_is_resumed_not_restarted(self):
        """Die Sitzungs-Kennung bleibt — sonst faengt der Agent nach jedem
        Schluckauf bei null an."""
        self.assertIn("Stable session id so a reconnect resumes the SAME conversation", UI)


if __name__ == "__main__":
    unittest.main()
