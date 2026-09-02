"""Auch der CHAT-Weg des eigenen Modells meldet den Zustand des Zugangs.

Issue #660 brachte die Statusmeldung fuer den Claude-Chat, den Codex-Chat und
den Codex-Aufgaben-Weg. Der Chat-Weg des eigenen Modells blieb dabei uebrig:
Wer sein Modell nur im Chat benutzt, haette einen abgelaufenen Zugang nirgends
gesehen — der Agent haette einfach nicht mehr geantwortet. Die Harness-Paritaet
ist hier keine Kosmetik, sondern der Unterschied zwischen einem sichtbaren und
einem unsichtbaren Ausfall.

Geprueft wird die ECHTE Meldefunktion mit einem Doppel fuer den Netzweg.
"""

import asyncio
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ai_credential_status  # noqa: E402

_QUELLE = (Path(__file__).resolve().parents[1] / "app" / "llm_chat_handler.py").read_text()


class DieMeldungIstAnAllenDreiEndenVerdrahtetTests(unittest.TestCase):
    def test_der_erfolgsfall_meldet(self):
        """Ohne das bliebe ein einmal rot markierter Zugang fuer immer rot."""
        block = _QUELLE.split('"status": "completed",', 1)[1][:900]
        self.assertIn("report_result_status(result)", block)

    def test_der_fehlerfall_im_zug_meldet(self):
        block = _QUELLE.split("_heal_after_context_overflow(message_id, event.text)", 1)[1][:600]
        self.assertIn("report_result_status(result)", block)

    def test_der_ausnahmefall_meldet(self):
        block = _QUELLE.split("_heal_after_context_overflow(message_id, failure_text)", 1)[1][:400]
        self.assertIn("report_result_status(result)", block)

    def test_gemeldet_wird_vor_dem_abschluss(self):
        """Nach `done` beendet der Aufrufer die Sitzung — eine Meldung danach
        koennte je nach Ablauf verlorengehen."""
        for anker in ("event.text)", "failure_text)"):
            block = _QUELLE.split("_heal_after_context_overflow(message_id, " + anker, 1)[1][:600]
            melden = block.index("report_result_status(result)")
            fertig = block.index('publish_chat(message_id, "done"')
            self.assertLess(melden, fertig)


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
