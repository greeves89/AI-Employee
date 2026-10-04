"""Chat-Kosten: jede Laufzeit meldet, was DIESE Nachricht gekostet hat (#896/#898).

Live belegt: alle Claude-Antworten standen mit ``cost_usd = 0`` in der Datenbank,
Codex mit ``NULL``. Dashboard 0 €, und die Budgetprüfung im Chat griff nie.

* **Claude Code** meldet ``total_cost_usd`` — gelesen wurde ``cost_usd`` (gibt es
  im result-Ereignis nicht). Und: die Summe ist FORTLAUFEND über die Sitzung. Der
  Chat setzt jede Nachricht mit ``--resume`` fort, die CLI rechnet dann ab dem im
  Verlauf gespeicherten Stand weiter („the first result already carries the
  earlier turns"). Die Kosten einer Nachricht sind also die Differenz zum Stand
  nach der vorigen.
* **Codex** meldet gar keine Kosten, nur Token — und auch die fortlaufend über
  den Faden: ``codex exec resume`` lieferte am 04.10.2026 nachgemessen
  13130/5 nach dem ersten und 27506/11 nach dem zweiten Zug (Einzelzug:
  14376/6). Kosten = Token-Anteil dieses Laufs × Preis aus ``model_registry`` —
  dieselbe Preistabelle wie Custom-LLM, keine zweite.
* **Custom-LLM** rechnete schon richtig; geprüft, dass es so bleibt.

Die Ereignisformen sind die der echten CLIs (Feldnamen aus deren Ausgabe).
"""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from app import model_registry


def _claude_result(gesamt, *, sid="s1", fehler=False, usage=None):
    ereignis = {
        "type": "result", "subtype": "success", "is_error": fehler,
        "result": "Antwort", "session_id": sid, "num_turns": 1, "duration_ms": 1200,
        "total_cost_usd": gesamt,
        "usage": usage or {"input_tokens": 10, "output_tokens": 20,
                           "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }
    if fehler:
        ereignis["errors"] = ["etwas ging schief"]
    return ereignis


class _Strom:
    def __init__(self, zeilen):
        self._daten = [(json.dumps(z) + "\n").encode() for z in zeilen]

    async def read(self, _n=-1):
        return self._daten.pop(0) if self._daten else b""

    async def readline(self):
        return self._daten.pop(0) if self._daten else b""


class _Stdin:
    def write(self, _d):
        pass

    async def drain(self):
        pass

    def close(self):
        pass


class _Prozess:
    def __init__(self, zeilen, rc=0):
        self.stdout = _Strom(zeilen)
        self.stderr = _Strom([])
        self.stdin = _Stdin()
        self.returncode = rc
        self._rc = rc

    async def wait(self):
        return self._rc

    def send_signal(self, _sig):
        pass


class ClaudeChatKosten(unittest.IsolatedAsyncioTestCase):
    def _handler(self):
        from app.chat_handler import ChatHandler

        pub = AsyncMock()
        pub.last_activity_at = 0.0
        return ChatHandler(pub)

    async def _lauf(self, handler, *zeilen):
        aufruf = AsyncMock(return_value=_Prozess(list(zeilen)))
        with patch("app.chat_handler.asyncio.create_subprocess_exec", aufruf), \
             patch("app.chat_handler.get_oauth_token", return_value=""):
            return await handler._execute_cli("m1", "hi", "claude-sonnet-5")

    async def test_neue_sitzung_meldet_den_betrag_der_cli(self):
        h = self._handler()
        erg = await self._lauf(h, {"type": "system", "subtype": "init", "session_id": "s1"},
                               _claude_result(0.0123))
        self.assertAlmostEqual(erg["cost_usd"], 0.0123)

    async def test_fortgesetzte_sitzung_meldet_nur_den_zuwachs(self):
        """Die zweite Nachricht derselben Sitzung kostet 0,0077 — nicht 0,02."""
        h = self._handler()
        await self._lauf(h, _claude_result(0.0123))
        self.assertEqual(h.session_id, "s1")
        erg = await self._lauf(h, _claude_result(0.0200))
        self.assertAlmostEqual(erg["cost_usd"], 0.0077)
        erg = await self._lauf(h, _claude_result(0.0250))
        self.assertAlmostEqual(erg["cost_usd"], 0.0050)

    async def test_wiederhergestellter_stand_gilt(self):
        """Nach einem Neustart kommt der Stand mit der Sitzung aus Redis."""
        h = self._handler()
        h.session_id = "s1"
        h._kosten_stand = 0.50
        erg = await self._lauf(h, _claude_result(0.53))
        self.assertAlmostEqual(erg["cost_usd"], 0.03)

    async def test_unbekannter_stand_rechnet_aus_token_statt_die_summe_zu_nehmen(self):
        """Fortgesetzte Sitzung ohne gemerkten Stand (gespeichert vor diesem
        Update): Die Summe trägt den ganzen Verlauf — die wäre viel zu hoch.
        Dann gilt der Token-Anteil dieses Laufs."""
        h = self._handler()
        h.session_id = "s1"
        h._kosten_stand = None
        usage = {"input_tokens": 1000, "output_tokens": 2000,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        erg = await self._lauf(h, _claude_result(12.0, usage=usage))
        erwartet = model_registry.estimate_cost("claude-sonnet-5", 1000, 2000)
        self.assertGreater(erwartet, 0)
        self.assertAlmostEqual(erg["cost_usd"], erwartet)
        # Ab jetzt ist der Stand bekannt.
        erg = await self._lauf(h, _claude_result(12.5))
        self.assertAlmostEqual(erg["cost_usd"], 0.5)

    async def test_neue_sitzung_beginnt_bei_null(self):
        """Wird die Sitzung verworfen (zu langer Verlauf, /reset), beginnt die
        Summe der CLI neu — der alte Stand darf nichts abziehen."""
        h = self._handler()
        await self._lauf(h, _claude_result(0.80))
        h.session_id = None
        erg = await self._lauf(h, _claude_result(0.05, sid="s2"))
        self.assertAlmostEqual(erg["cost_usd"], 0.05)

    async def test_auch_ein_fehlerlauf_hat_gekostet(self):
        h = self._handler()
        erg = await self._lauf(h, _claude_result(0.04, fehler=True))
        self.assertEqual(erg["status"], "error")
        self.assertAlmostEqual(erg["cost_usd"], 0.04)

    async def test_genullte_absturzsumme_setzt_den_stand_nicht_zurueck(self):
        """„Crash/startup-error results may carry zeroed values" — eine 0 ist
        kein Neubeginn der Summe."""
        h = self._handler()
        await self._lauf(h, _claude_result(0.30))
        erg = await self._lauf(h, _claude_result(0, fehler=True))
        self.assertEqual(erg.get("cost_usd", 0), 0)
        erg = await self._lauf(h, _claude_result(0.35))
        self.assertAlmostEqual(erg["cost_usd"], 0.05)

    async def test_done_ereignis_traegt_den_betrag(self):
        """Ende-zu-Ende im Agenten: Was ``handle_message`` als ``done``
        veröffentlicht, trägt den Betrag — das liest der Orchestrator."""
        h = self._handler()
        h.pending_drain = None
        aufruf = AsyncMock(return_value=_Prozess([_claude_result(0.0123)]))
        with patch("app.chat_handler.asyncio.create_subprocess_exec", aufruf), \
             patch("app.chat_handler.get_oauth_token", return_value=""):
            await h.handle_message("m1", "hi", model="claude-sonnet-5")
        done = [c.args[2] for c in h.log_publisher.publish_chat.call_args_list
                if c.args[1] == "done"]
        self.assertEqual(len(done), 1)
        self.assertAlmostEqual(done[0]["cost_usd"], 0.0123)


class KostenStandUeberlebtNeustart(unittest.IsolatedAsyncioTestCase):
    """Der Stand wird mit der Sitzung in Redis abgelegt und zurückgeholt."""

    async def test_stand_reist_mit_der_sitzung(self):
        from app.chat_consumer import ChatConsumer
        from app.chat_handler import ChatHandler

        class _R:
            def __init__(self):
                self.daten = {}

            async def setex(self, k, _ttl, v):
                self.daten[k] = v

            async def get(self, k):
                return self.daten.get(k)

        redis = _R()
        consumer = ChatConsumer.__new__(ChatConsumer)
        consumer.redis = redis
        consumer.agent_id = "a1"
        consumer._handlers = {}

        pub = AsyncMock()
        alt = ChatHandler(pub)
        alt.session_id = "s1"
        alt._kosten_stand = 1.25
        await consumer._persist_session("webapp:x", alt, "claude-sonnet-5")

        with patch("app.chat_consumer.settings") as st:
            st.agent_mode = "claude_code"
            neu = await consumer._get_or_create_handler("webapp:x", model="claude-sonnet-5", log_publisher=pub)
        self.assertEqual(neu.session_id, "s1")
        self.assertAlmostEqual(neu._kosten_stand, 1.25)


class SteuerungSummiertAlleZuege(unittest.IsolatedAsyncioTestCase):
    async def test_eingefaltete_zuege_zaehlen_mit(self):
        from app.steering import run_turns_with_steering

        ergebnisse = [
            {"status": "completed", "text": "a", "cost_usd": 0.10,
             "input_tokens": 100, "output_tokens": 10},
            {"status": "completed", "text": "b", "cost_usd": 0.05,
             "input_tokens": 50, "output_tokens": 5},
        ]
        warteschlange = [["nachgereicht"], []]

        async def lauf(_t, _r):
            return ergebnisse.pop(0)

        async def drain():
            return warteschlange.pop(0) if warteschlange else []

        erg = await run_turns_with_steering(
            initial_text="los", run_turn=lauf, stop_current=AsyncMock(),
            pending_drain=drain, poll_interval=0.01,
        )
        self.assertEqual(erg["text"], "b")
        self.assertAlmostEqual(erg["cost_usd"], 0.15)
        self.assertEqual(erg["input_tokens"], 150)
        self.assertEqual(erg["output_tokens"], 15)


def _zeile(ereignis):
    return (json.dumps(ereignis) + "\n").encode()


# Echte Form von ``codex exec --json`` (codex-cli 0.159.3, 04.10.2026).
def _codex_lauf(eingabe, gecacht, ausgabe):
    return [
        _zeile({"type": "thread.started", "thread_id": "t-1"}),
        _zeile({"type": "turn.started"}),
        _zeile({"type": "item.completed", "item": {"id": "i0", "type": "agent_message",
                                                   "text": "eins"}}),
        _zeile({"type": "turn.completed", "usage": {
            "input_tokens": eingabe, "cached_input_tokens": gecacht,
            "cache_write_input_tokens": 0, "output_tokens": ausgabe,
            "reasoning_output_tokens": 0}}),
    ]


class CodexChatKosten(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.codex_runner import CodexAgentRunner
        from tests.test_codex_exit_preserves_partial_result import _FakeProcess

        self._FakeProcess = _FakeProcess
        pub = AsyncMock()
        pub.last_activity_at = 0.0
        self.runner = CodexAgentRunner(pub)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.dict(os.environ, {"CODEX_HOME": self.tmp.name}),
                  patch("app.codex_runner._codex_auth_problem", return_value=None),
                  patch("app.codex_runner.codex_auth_sync.push_if_rotated",
                        AsyncMock(return_value=False))):
            p.start()
            self.addCleanup(p.stop)

    async def _lauf(self, zeilen, resume=False):
        with patch("app.codex_runner.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=self._FakeProcess(zeilen, [], 0))):
            return await self.runner._run_codex("m1", "p", "gpt-5.3-codex",
                                                stream="chat", resume=resume)

    async def test_ein_lauf_hat_einen_betrag(self):
        erg = await self._lauf(_codex_lauf(13130, 9600, 5))
        erwartet = model_registry.estimate_cost("gpt-5.3-codex", 13130, 5)
        self.assertGreater(erwartet, 0)
        self.assertAlmostEqual(erg["cost_usd"], erwartet)
        self.assertEqual(erg["input_tokens"], 13130)
        self.assertEqual(erg["output_tokens"], 5)

    async def test_fortgesetzter_faden_zaehlt_nur_seinen_anteil(self):
        """Gemessen: 13130/5, nach ``resume`` 27506/11 — der zweite Zug selbst
        hat 14376/6 verbraucht."""
        await self._lauf(_codex_lauf(13130, 9600, 5))
        erg = await self._lauf(_codex_lauf(27506, 22272, 11), resume=True)
        self.assertEqual(erg["input_tokens"], 14376)
        self.assertEqual(erg["output_tokens"], 6)
        self.assertEqual(erg["cached_tokens"], 12672)
        self.assertAlmostEqual(erg["cost_usd"],
                               model_registry.estimate_cost("gpt-5.3-codex", 14376, 6))

    async def test_frischer_lauf_beginnt_bei_null(self):
        await self._lauf(_codex_lauf(13130, 9600, 5))
        erg = await self._lauf(_codex_lauf(9000, 0, 4), resume=False)
        self.assertEqual(erg["input_tokens"], 9000)

    async def test_done_des_chats_traegt_den_betrag(self):
        from app.codex_runner import CodexChatHandler

        pub = AsyncMock()
        pub.last_activity_at = 0.0
        h = CodexChatHandler(pub)
        h.pending_drain = None
        with patch("app.codex_runner.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=self._FakeProcess(_codex_lauf(13130, 9600, 5), [], 0))), \
             patch("app.codex_runner.report_result_status", AsyncMock()):
            await h.handle_message("m1", "hi", model="gpt-5.3-codex")
        done = [c.args[2] for c in pub.publish_chat.call_args_list if c.args[1] == "done"]
        self.assertEqual(len(done), 1)
        self.assertGreater(done[0]["cost_usd"], 0)


class CustomLlmChatKosten(unittest.IsolatedAsyncioTestCase):
    async def test_done_traegt_den_betrag(self):
        from app.config import settings
        from app.llm_chat_handler import LLMChatHandler
        from app.providers.base import ChatMessage
        from tests.test_werkzeug_eintrag import _Anbieter, _Publisher

        pub = _Publisher()
        h = LLMChatHandler(log_publisher=pub)
        h._context_window = 1_000_000
        h._history = [ChatMessage(role="system", content="S")]
        h._tool_executor.execute = AsyncMock(return_value="ok")
        with patch.object(settings, "llm_model_name", "gpt-4o"), \
             patch.object(h, "_get_provider", return_value=_Anbieter()), \
             patch.object(h, "_get_tools", new=AsyncMock(return_value=None)), \
             patch("app.llm_chat_handler.report_result_status", new=AsyncMock()):
            erg = await h.handle_message("m1", "mach was")
        self.assertGreater(erg["cost_usd"], 0)
        done = [d for art, d in pub.events if art == "done"]
        self.assertGreater(done[-1]["cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
