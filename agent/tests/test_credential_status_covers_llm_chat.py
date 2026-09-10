"""Auch der CHAT-Weg des eigenen Modells meldet den Zustand des Zugangs.

Issue #660 brachte die Statusmeldung fuer den Claude-Chat, den Codex-Chat und
den Codex-Aufgaben-Weg. Der Chat-Weg des eigenen Modells blieb dabei uebrig:
Wer sein Modell nur im Chat benutzt, haette einen abgelaufenen Zugang nirgends
gesehen — der Agent haette einfach nicht mehr geantwortet. Die Harness-Paritaet
ist hier keine Kosmetik, sondern der Unterschied zwischen einem sichtbaren und
einem unsichtbaren Ausfall.

Geprueft wird die ECHTE Meldefunktion mit einem Doppel fuer den Netzweg — und
der echte Chat-Zug mit einem Doppel fuer das Modell. Frueher stand hier
stattdessen eine Quelltext-Suche in einem festen Zeichenfenster. Die hat
zweierlei nicht gemerkt: ein fehlendes `await` (die Meldung wird nie
abgeschickt) und einen auskommentierten Aufruf (der Text steht ja noch da).
Ausserdem wurde sie rot, sobald dem Ergebnis ein Feld hinzukam.
"""

import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ai_credential_status  # noqa: E402
from app import llm_chat_handler  # noqa: E402
from app.llm_chat_handler import LLMChatHandler  # noqa: E402


class _Mitschrift:
    """Haelt fest, was in welcher Reihenfolge nach draussen ging."""

    def __init__(self):
        self.ereignisse: list[str] = []

    async def publish_chat(self, message_id, typ, daten=None):
        self.ereignisse.append(typ)


def _ereignis(typ, **felder):
    vorgabe = dict(type=typ, text="", tool_id=None, tool_name=None,
                   tool_input=None, input_tokens=0, output_tokens=0)
    return SimpleNamespace(**{**vorgabe, **felder})


class _Modell:
    """Doppel fuer den Anbieter: gibt die vorgegebenen Ereignisse aus — oder
    fliegt, wenn eine Ausnahme vorgegeben ist."""

    def __init__(self, ereignisse=(), ausnahme=None):
        self._ereignisse = list(ereignisse)
        self._ausnahme = ausnahme
        self.reasoning_effort = None

    async def stream_completion(self, verlauf, werkzeuge):
        if self._ausnahme is not None:
            raise self._ausnahme
        for e in self._ereignisse:
            yield e


class DieMeldungIstAnAllenDreiEndenVerdrahtetTests(unittest.IsolatedAsyncioTestCase):
    """Ein echter Zug wird durchgefahren; geprueft wird, ob die Meldung
    tatsaechlich ABGESCHICKT wurde und ob sie VOR dem `done` kam. Nach `done`
    beendet der Aufrufer die Sitzung — eine Meldung danach koennte je nach
    Ablauf verlorengehen."""

    async def _zug(self, modell):
        h = LLMChatHandler.__new__(LLMChatHandler)
        h.log_publisher = self.mitschrift = _Mitschrift()
        h.is_running = False
        h._stopping = False
        h._history = [llm_chat_handler.ChatMessage(role="system", content="x")]
        h._loop_detector = SimpleNamespace(reset=lambda: None,
                                           check=lambda *a, **k: None,
                                           record=lambda *a, **k: None)
        h._last_input_tokens = 0
        h._overhead_tokens = 0
        h._compaction_floor = 0
        h._connection_retries = 0
        h._models_tried = set()
        h.pending_drain = None
        h._get_provider = lambda: modell
        h._get_tools = unittest.mock.AsyncMock(return_value=[])
        h._needs_compaction = lambda: False
        h._heal_after_context_overflow = unittest.mock.AsyncMock()
        h._retry_after_connection_glitch = unittest.mock.AsyncMock(return_value=False)
        h._switch_to_fallback = unittest.mock.AsyncMock(return_value=False)

        # Der Merker haelt fest, WAS gemeldet wurde und WIE VIELE Ereignisse zu
        # dem Zeitpunkt schon draussen waren — daraus faellt die Reihenfolge ab.
        gemeldet: list[tuple[dict, int]] = []

        async def merker(result):
            gemeldet.append((result, len(self.mitschrift.ereignisse)))

        with unittest.mock.patch.object(llm_chat_handler, "report_result_status", merker):
            ergebnis = await h.handle_message("m1", "hallo")
        return ergebnis, gemeldet

    def _pruefe(self, gemeldet, erwarteter_status):
        self.assertEqual(len(gemeldet), 1, "genau eine Meldung erwartet")
        result, vor_wie_vielen = gemeldet[0]
        self.assertEqual(result.get("status"), erwarteter_status)
        self.assertIn("done", self.mitschrift.ereignisse)
        self.assertLess(vor_wie_vielen, self.mitschrift.ereignisse.index("done") + 1,
                        "die Meldung kam erst nach dem Abschluss")

    async def test_der_erfolgsfall_meldet(self):
        """Ohne das bliebe ein einmal rot markierter Zugang fuer immer rot."""
        _, gemeldet = await self._zug(_Modell([
            _ereignis("text_delta", text="hallo"),
            _ereignis("done", input_tokens=10, output_tokens=3),
        ]))
        self._pruefe(gemeldet, "completed")

    async def test_der_fehlerfall_im_zug_meldet(self):
        _, gemeldet = await self._zug(_Modell([
            _ereignis("error", text="OAuth token_expired"),
        ]))
        self._pruefe(gemeldet, "error")

    async def test_der_ausnahmefall_meldet(self):
        _, gemeldet = await self._zug(_Modell(ausnahme=RuntimeError("Netz weg")))
        self._pruefe(gemeldet, "error")


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
