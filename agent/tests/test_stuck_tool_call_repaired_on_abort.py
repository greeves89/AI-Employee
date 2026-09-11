"""Ein abgebrochener Zug machte jede weitere Nachricht der Sitzung kaputt (SKBS, 2026-09-11).

Der 600s-Leerlauf-Wächter (``chat_consumer.py``) bricht einen haengenden Zug per
``asyncio.Task.cancel()`` ab. Landet das WAEHREND ein Werkzeug noch laeuft — also
NACHDEM die Assistenten-Nachricht mit ``tool_calls`` schon in ``self._history``
steht, aber BEVOR das passende Werkzeugergebnis angehaengt wurde — blieb die
Historie fuer den Rest des Prozesses kaputt: jede weitere Anfrage an den
Provider (Azure/OpenAI) schlug mit "no tool output found for function call ..."
(HTTP 400) fehl, bis der Container neu startete. Ein Kunde traf das live, nachdem
sein "status?" auf genau diesen Fehler lief.

``_repair_dangling_tool_calls`` schliesst die Luecke: ein synthetisches
Werkzeugergebnis pro verwaistem ``tool_call_id``, damit der naechste Zug wieder
ein fuer den Provider gueltiges Gespraech sieht.
"""

import unittest

from app.llm_chat_handler import LLMChatHandler
from app.providers.base import ChatMessage


def _handler() -> LLMChatHandler:
    return LLMChatHandler(log_publisher=None)  # nie benutzt vom Reparatur-Pfad


def _dangling_tool_call(call_id: str = "call_abc123", name: str = "bash") -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=None,
        tool_calls=[{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }],
    )


class TheDanglingCallGetsAnAnswerTests(unittest.TestCase):
    def test_an_orphaned_tool_call_is_closed_out(self):
        h = _handler()
        h._history = [
            ChatMessage(role="user", content="mach das mal"),
            _dangling_tool_call("call_abc123", "bash"),
        ]
        h._repair_dangling_tool_calls()
        self.assertEqual(h._history[-1].role, "tool")
        self.assertEqual(h._history[-1].tool_call_id, "call_abc123")

    def test_every_orphaned_call_in_the_message_gets_its_own_answer(self):
        """Ein Zug kann mehrere Werkzeuge parallel aufrufen — jedes davon
        braucht laut Provider-Vertrag sein EIGENES Ergebnis."""
        h = _handler()
        h._history = [
            ChatMessage(role="user", content="zwei dinge bitte"),
            ChatMessage(
                role="assistant", content=None,
                tool_calls=[
                    {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "call_2", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
                ],
            ),
        ]
        h._repair_dangling_tool_calls()
        tool_replies = [m for m in h._history if m.role == "tool"]
        self.assertEqual({m.tool_call_id for m in tool_replies}, {"call_1", "call_2"})


class ItLeavesHealthyHistoryAloneTests(unittest.TestCase):
    def test_a_call_that_already_has_its_result_is_untouched(self):
        """Der Normalfall — ein sauber abgeschlossener Zug — darf nicht
        veraendert werden, sonst kommt eine Doppelantwort in die Historie."""
        h = _handler()
        h._history = [
            ChatMessage(role="user", content="mach das"),
            _dangling_tool_call("call_xyz", "bash"),
            ChatMessage(role="tool", content="ok", tool_call_id="call_xyz", name="bash"),
        ]
        vorher = len(h._history)
        h._repair_dangling_tool_calls()
        self.assertEqual(len(h._history), vorher)

    def test_a_plain_text_reply_is_untouched(self):
        h = _handler()
        h._history = [
            ChatMessage(role="user", content="wie geht's?"),
            ChatMessage(role="assistant", content="Gut, danke!"),
        ]
        vorher = list(h._history)
        h._repair_dangling_tool_calls()
        self.assertEqual(h._history, vorher)

    def test_an_empty_history_does_not_crash(self):
        h = _handler()
        h._history = []
        h._repair_dangling_tool_calls()  # darf nicht werfen
        self.assertEqual(h._history, [])


class ItIsWiredIntoTheAbortPathTests(unittest.TestCase):
    """Ohne diese Verdrahtung repariert die Funktion nichts von selbst."""

    def test_stop_current_calls_the_repair(self):
        import inspect

        quelle = inspect.getsource(LLMChatHandler.stop_current)
        self.assertIn("_repair_dangling_tool_calls", quelle)


class TheWatchdogWaitsForTheCancelToSettleTests(unittest.TestCase):
    """``turn.cancel()`` liefert die Ausnahme nur asynchron — ohne ein Warten
    darauf kann die Reparatur laufen, waehrend der Zug noch mitten in der
    Werkzeugausfuehrung steckt und die Historie gerade selbst veraendert."""

    import pathlib

    _SRC = (pathlib.Path(__file__).resolve().parents[1] / "app" / "chat_consumer.py").read_text()

    def test_await_turn_precedes_stop_current_in_the_abort_handler(self):
        # Vom Log-Aufruf bis zum naechsten publish_chat("error", ...) danach —
        # ein stabiler Anker statt einer Zeichenzahl, die bei jeder Kommentar-
        # Aenderung im Block neu kalibriert werden muesste.
        nach_log = self._SRC.split(
            '"Chat turn %s aborted — no activity for %ss (agent appears stuck)"', 1
        )[1]
        block = nach_log.split('message_id, "error",', 1)[0]
        await_pos = block.find("await turn")
        stop_pos = block.find("await handler.stop_current()")
        self.assertNotEqual(await_pos, -1, "kein Warten auf den abgebrochenen Zug gefunden")
        self.assertNotEqual(stop_pos, -1, "stop_current()-Aufruf nicht gefunden")
        self.assertLess(await_pos, stop_pos, "muss VOR stop_current() stehen")


if __name__ == "__main__":
    unittest.main()
