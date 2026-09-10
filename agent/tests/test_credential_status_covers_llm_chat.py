"""Auch der CHAT-Weg des eigenen Modells meldet den Zustand des Zugangs.

Issue #660 brachte die Statusmeldung fuer den Claude-Chat, den Codex-Chat und
den Codex-Aufgaben-Weg. Der Chat-Weg des eigenen Modells blieb dabei uebrig:
Wer sein Modell nur im Chat benutzt, haette einen abgelaufenen Zugang nirgends
gesehen — der Agent haette einfach nicht mehr geantwortet. Die Harness-Paritaet
ist hier keine Kosmetik, sondern der Unterschied zwischen einem sichtbaren und
einem unsichtbaren Ausfall.

Geprueft wird die ECHTE Meldefunktion mit einem Doppel fuer den Netzweg.

Dass die Meldung an allen drei Enden haengt, stand vorher als Textsuche in
Zeichenfenstern (900/600/400) auf ``llm_chat_handler.py``. Das Fenster des
Erfolgsfalls ist im September 2026 uebergelaufen — jemand schrieb vier Zeilen
Kommentar in die Ergebnis-Ablage, und die Hauptlinie wurde rot, obwohl die
Meldung ordentlich abgesetzt wurde. Ein Zeichenfenster misst Abstand, gemeint
war Reihenfolge; siehe Issue #726. Jetzt wird der Zug wirklich gefahren.
"""

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ai_credential_status  # noqa: E402
from app.providers.base import ChatMessage, LLMEvent  # noqa: E402


class _Verlauf:
    """Eine gemeinsame Zeitleiste fuer Chat-Ereignisse UND Zugangsmeldungen.

    Beides in EINER Liste — nur so ist die Reihenfolge zwischen „gemeldet" und
    „done" ueberhaupt beobachtbar, ohne wieder im Quelltext nachzusehen.
    """

    def __init__(self):
        self.schritte: list[tuple[str, dict]] = []

    async def publish_chat(self, message_id, kind, payload):
        self.schritte.append((kind, payload))

    async def melde(self, result):
        self.schritte.append(("gemeldet", result))

    @property
    def namen(self) -> list[str]:
        return [k for k, _ in self.schritte]

    def gemeldetes(self) -> dict | None:
        for name, nutzlast in self.schritte:
            if name == "gemeldet":
                return nutzlast
        return None


class _Anbieter:
    """Spielt eine feste Ereignisfolge ab — oder wirft."""

    def __init__(self, ereignisse=(), exc=None, davor=None):
        self.ereignisse = list(ereignisse)
        self.exc = exc
        # ``davor`` laeuft WAEHREND gelesen wird. Ein Stop-Klick vor dem Start
        # waere etwas anderes: ``handle_message`` setzt die Marke bei jeder
        # Nachricht bewusst zurueck.
        self.davor = davor
        self.reasoning_effort = ""

    def stream_completion(self, messages, tools=None):
        ereignisse, exc, davor = self.ereignisse, self.exc, self.davor

        async def gen():
            if davor is not None:
                await davor()
            for e in ereignisse:
                yield e
            if exc is not None:
                raise exc

        return gen()

    async def close(self):
        pass


class DieMeldungIstAnAllenDreiEndenVerdrahtetTests(unittest.IsolatedAsyncioTestCase):
    """Erfolg, Fehler-Ereignis im Zug, Ausnahme — jedes Ende meldet selbst.

    Die drei Enden liegen im Quelltext weit auseinander; genau deshalb wurde
    beim Nachruesten von #660 eines vergessen. Hier hat jedes seinen Test.
    """

    def _handler(self, verlauf):
        from app.llm_chat_handler import LLMChatHandler

        h = LLMChatHandler(log_publisher=verlauf)
        h._context_window = 1_000_000
        # Vorbelegt, damit kein Systemprompt gebaut wird — der liest /workspace
        # und ist hier nicht die Frage.
        h._history = [ChatMessage(role="system", content="S")]
        return h

    async def _fahre(self, verlauf, anbieter, handler=None):
        import app.llm_chat_handler as modul

        h = handler or self._handler(verlauf)
        with unittest.mock.patch.object(h, "_get_provider", return_value=anbieter), \
             unittest.mock.patch.object(
                 h, "_get_tools", new=unittest.mock.AsyncMock(return_value=None)), \
             unittest.mock.patch.object(modul, "report_result_status", new=verlauf.melde):
            return await h.handle_message("m1", "mach was")

    async def test_der_erfolgsfall_meldet(self):
        """Ohne das bliebe ein einmal rot markierter Zugang fuer immer rot."""
        verlauf = _Verlauf()

        ergebnis = await self._fahre(verlauf, _Anbieter([
            LLMEvent(type="text_delta", text="Hallo"),
            LLMEvent(type="done", input_tokens=10, output_tokens=3),
        ]))

        self.assertEqual(ergebnis["status"], "completed")
        self.assertEqual(verlauf.gemeldetes()["status"], "completed")

    async def test_der_fehlerfall_im_zug_meldet(self):
        """Ein abgelaufener Zugang kommt als Fehler-EREIGNIS, nicht als Ausnahme
        — der Weg, auf dem der Nutzer ihn tatsaechlich zu sehen bekommt."""
        verlauf = _Verlauf()

        ergebnis = await self._fahre(verlauf, _Anbieter([
            LLMEvent(type="error", text="OAuth token_expired"),
        ]))

        self.assertEqual(ergebnis["status"], "error")
        self.assertEqual(verlauf.gemeldetes()["error"], "OAuth token_expired")

    async def test_der_ausnahmefall_meldet(self):
        verlauf = _Verlauf()

        ergebnis = await self._fahre(
            verlauf, _Anbieter(exc=RuntimeError("OAuth token_expired")))

        self.assertEqual(ergebnis["status"], "error")
        self.assertIn("OAuth token_expired", verlauf.gemeldetes()["error"])

    async def test_gemeldet_wird_vor_dem_abschluss(self):
        """Nach ``done`` beendet der Aufrufer die Sitzung — eine Meldung danach
        koennte je nach Ablauf verlorengehen."""
        faelle = {
            "erfolg": _Anbieter([LLMEvent(type="done")]),
            "fehler-ereignis": _Anbieter([LLMEvent(type="error", text="OAuth token_expired")]),
            "ausnahme": _Anbieter(exc=RuntimeError("kaputt")),
        }
        for name, anbieter in faelle.items():
            with self.subTest(fall=name):
                verlauf = _Verlauf()

                await self._fahre(verlauf, anbieter)

                namen = verlauf.namen
                self.assertIn("gemeldet", namen)
                self.assertEqual(namen[-1], "done")
                self.assertLess(namen.index("gemeldet"), namen.index("done"))

    async def test_ein_abbruch_faerbt_den_zugang_nicht(self):
        """Angehalten sagt nichts ueber den Zugang aus. Wuerde der Abbruch als
        „ok" durchgehen, machte ein Stop-Klick einen roten Zugang gruen."""
        verlauf = _Verlauf()
        h = self._handler(verlauf)
        # Der echte Ablauf: der Zug laeuft, dann drueckt jemand Stop, und erst
        # dadurch reisst der Strom.
        anbieter = _Anbieter(exc=RuntimeError("ReadError('')"), davor=h.stop_current)

        ergebnis = await self._fahre(verlauf, anbieter, handler=h)

        self.assertEqual(ergebnis["status"], "cancelled")
        self.assertNotIn("gemeldet", verlauf.namen)


class WasGemeldetWirdTests(unittest.IsolatedAsyncioTestCase):
    async def test_ein_abgelaufener_zugang_wird_als_fehler_gemeldet(self):
        with unittest.mock.patch.object(
            ai_credential_status, "report_ai_credential_status",
            new=unittest.mock.AsyncMock(),
        ) as melde:
            await ai_credential_status.report_result_status(
                {"status": "error", "error": "OAuth token_expired"}
            )
        melde.assert_awaited_once_with("auth_failed")

    async def test_ein_gelungener_lauf_macht_ihn_wieder_gesund(self):
        with unittest.mock.patch.object(
            ai_credential_status, "report_ai_credential_status",
            new=unittest.mock.AsyncMock(),
        ) as melde:
            await ai_credential_status.report_result_status({"status": "completed"})
        melde.assert_awaited_once_with("ok")

    async def test_ein_gewoehnlicher_fehler_faerbt_den_zugang_nicht_rot(self):
        """Ein Werkzeugfehler sagt nichts ueber den Zugang aus — wuerde er ihn
        rot faerben, waere die Anzeige nach kurzer Zeit wertlos."""
        with unittest.mock.patch.object(
            ai_credential_status, "report_ai_credential_status",
            new=unittest.mock.AsyncMock(),
        ) as melde:
            await ai_credential_status.report_result_status(
                {"status": "error", "error": "FileNotFoundError: /tmp/x"}
            )
        melde.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
