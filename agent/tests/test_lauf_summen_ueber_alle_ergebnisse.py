"""Ein Lauf mit mehreren ``result``-Ereignissen zaehlt Dauer, Zuege und Tokens
ueber ALLE Abschnitte, die Kosten nur einmal.

Anlass im Betrieb: Ein Zeitplan-Lauf wartete im Hintergrund auf CI und Merges.
Jedes Aufwachen ist ein eigener Abschnitt mit eigenem ``result``. Der Runner
ueberschrieb die Werte mit dem letzten Abschnitt — gebucht standen 71 $ fuer
0,7 Minuten und 4 Zuege, tatsaechlich lief er 71 Minuten. Die Token-Anzeige
unter Finanzen zaehlte dadurch zu wenig.
"""

import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import agent_runner as ar
from app.log_publisher import LogPublisher


def _ergebnis(dauer, zuege, kosten, ein, aus, text):
    return {"type": "result", "duration_ms": dauer, "num_turns": zuege,
            "total_cost_usd": kosten, "usage": {"input_tokens": ein, "output_tokens": aus},
            "result": text}


class Einrechnen(unittest.TestCase):
    def test_erstes_ergebnis_wird_uebernommen(self):
        d = ar.ergebnis_uebernehmen(None, _ergebnis(1000, 3, 0.5, 10, 20, "a"), [])
        self.assertEqual((d["duration_ms"], d["num_turns"], d["cost_usd"],
                          d["input_tokens"], d["output_tokens"], d["result"]),
                         (1000, 3, 0.5, 10, 20, "a"))

    def test_fehlende_werte_brechen_die_summe_nicht(self):
        d = ar.ergebnis_uebernehmen(None, {"type": "result", "result": "a"}, [])
        d = ar.ergebnis_uebernehmen(d, _ergebnis(500, 2, 1.0, 5, 6, "b"), [])
        self.assertEqual((d["duration_ms"], d["num_turns"], d["input_tokens"]), (500, 2, 5))

    def test_kosten_sinken_nie(self):
        """Fortlaufende Summe — ein kleinerer Wert darf den Lauf nicht billiger machen."""
        d = ar.ergebnis_uebernehmen(None, _ergebnis(1, 1, 5.0, 1, 1, "a"), [])
        d = ar.ergebnis_uebernehmen(d, _ergebnis(1, 1, 0.0, 1, 1, "b"), [])
        self.assertEqual(d["cost_usd"], 5.0)

    def test_fehlerkennzeichen_kommt_vom_letzten_abschnitt(self):
        d = ar.ergebnis_uebernehmen(None, {**_ergebnis(1, 1, 1, 1, 1, "x"), "is_error": True}, [])
        d = ar.ergebnis_uebernehmen(d, _ergebnis(1, 1, 2, 1, 1, "fertig"), [])
        self.assertFalse(d["is_error"])
        self.assertEqual(d["is_error_text"], "")


class RunnerZaehltAlleAbschnitte(unittest.IsolatedAsyncioTestCase):
    async def test_drei_abschnitte_eines_laufs(self):
        echtes_starten = asyncio.create_subprocess_exec

        async def starten(*cmd, **kw):
            return await echtes_starten("true", **kw)

        async def stream(_proc):
            yield _ergebnis(60_000, 40, 10.0, 100, 1000, "warte auf CI")
            yield _ergebnis(30_000, 20, 50.0, 50, 500, "CI gruen, warte auf Merge")
            yield _ergebnis(42_000, 4, 71.08, 8, 2118, "fertig")

        publisher = MagicMock(spec=LogPublisher)
        publisher.publish = AsyncMock()
        runner = ar.AgentRunner(publisher)
        with patch.object(ar.settings, "workspace_dir", tempfile.mkdtemp()), \
                patch.object(ar.asyncio, "create_subprocess_exec", starten), \
                patch.object(ar, "compose_prompt_bundle", lambda p, l: ""), \
                patch.object(runner, "_stream_output", stream), \
                patch("app.runner_hooks.zeitkontext", lambda: ""):
            d = await runner._execute_task_once("t1", "Auftrag")

        self.assertEqual(d["status"], "completed", d)
        self.assertEqual(d["duration_ms"], 132_000)
        self.assertEqual(d["num_turns"], 64)
        self.assertEqual(d["input_tokens"], 158)
        self.assertEqual(d["output_tokens"], 3618)
        self.assertAlmostEqual(d["cost_usd"], 71.08)
        self.assertEqual(d["result"], "fertig")


if __name__ == "__main__":
    unittest.main()
