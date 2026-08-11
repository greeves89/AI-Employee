"""Stop drücken ist kein Fehler.

Der Anlass steht im Chat des Kunden: nach einem Klick auf Stop erschien
``Unexpected error: ReadError('')`` in Rot. Der Fehler war echt — nur hatte ihn
niemand gemacht: ``stop_current`` schliesst den laufenden HTTP-Strom, und das
Lesen darauf wirft in httpx einen ``ReadError``. Unser eigener Abbruch kam als
Störung zurück.

Der Claude-CLI-Pfad kannte das Problem längst und merkt sich ``_interrupted``
(``chat_handler.py``, SIGINT/Rückgabecode -2). Hier wird dieselbe Regel für die
Custom-LLM-Laufzeit festgehalten — Harness-Parität, kein Sonderweg.
"""

import unittest
from unittest.mock import AsyncMock, patch


class _Publisher:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish_chat(self, message_id, kind, payload):
        self.events.append((kind, payload))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]


class _Boom:
    """Ein Anbieter, dessen Strom abreisst — wie beim Schliessen mitten im Lesen.

    ``before`` wird ausgeführt, WÄHREND gelesen wird. Genau da liegt der echte
    Ablauf: der Zug läuft bereits, dann drückt jemand Stop, und erst dadurch
    reisst der Strom. Ein Test, der vorher stoppt, prüft etwas anderes — die
    Marke wird beim Start jeder Nachricht bewusst zurückgesetzt.
    """

    def __init__(self, exc, before=None):
        self.exc = exc
        self.before = before
        self.reasoning_effort = ""

    def stream_completion(self, messages, tools=None):
        exc, before = self.exc, self.before

        async def gen():
            if before is not None:
                await before()
            raise exc
            yield  # pragma: no cover — macht die Funktion zum Generator

        return gen()

    async def close(self):
        pass


class StopIsNotAnErrorTests(unittest.IsolatedAsyncioTestCase):
    def _handler(self, pub):
        from app.llm_chat_handler import LLMChatHandler
        from app.providers.base import ChatMessage

        h = LLMChatHandler(log_publisher=pub)
        h._context_window = 1_000_000
        # Vorbelegt, damit der Systemprompt nicht gebaut wird — der liest
        # /workspace und ist hier nicht die Frage.
        h._history = [ChatMessage(role="system", content="S")]
        return h

    async def _run(self, handler, pub, exc, *, stopped: bool):
        before = handler.stop_current if stopped else None
        with patch.object(handler, "_get_provider", return_value=_Boom(exc, before)), \
             patch.object(handler, "_get_tools", new=AsyncMock(return_value=None)):
            return await handler.handle_message("m1", "mach was")

    async def test_a_read_error_after_stop_is_reported_as_cancelled(self):
        pub = _Publisher()
        h = self._handler(pub)
        try:
            import httpx
            exc = httpx.ReadError("")
        except ImportError:  # pragma: no cover
            exc = OSError("")
        result = await self._run(h, pub, exc, stopped=True)
        self.assertEqual(result["status"], "cancelled")
        self.assertIn("cancelled", pub.kinds())
        self.assertNotIn("error", pub.kinds())

    async def test_the_same_failure_without_stop_is_still_an_error(self):
        """Die Unterscheidung darf nicht dazu führen, dass echte Abbrüche
        verschluckt werden — dann stünde der Nutzer vor einer stillen Wand."""
        pub = _Publisher()
        h = self._handler(pub)
        result = await self._run(h, pub, RuntimeError("Verbindung weg"), stopped=False)
        self.assertEqual(result["status"], "error")
        self.assertIn("error", pub.kinds())

    async def test_the_turn_is_closed_either_way(self):
        """Ohne abschliessendes ``done`` dreht sich die Anzeige weiter."""
        pub = _Publisher()
        h = self._handler(pub)
        await self._run(h, pub, RuntimeError("x"), stopped=True)
        self.assertEqual(pub.kinds()[-1], "done")

    async def test_the_flag_does_not_leak_into_the_next_turn(self):
        """Sonst käme die nächste echte Störung als „abgebrochen" durch."""
        pub = _Publisher()
        h = self._handler(pub)
        await self._run(h, pub, RuntimeError("x"), stopped=True)
        self.assertFalse(h._stopping)
        pub2 = _Publisher()
        h.log_publisher = pub2
        result = await self._run(h, pub2, RuntimeError("echt kaputt"), stopped=False)
        self.assertEqual(result["status"], "error")

    async def test_stopping_leaves_the_history_alone(self):
        """Angehalten heisst nicht verworfen — der nächste Zug setzt darauf auf."""
        pub = _Publisher()
        h = self._handler(pub)
        await self._run(h, pub, RuntimeError("x"), stopped=True)
        self.assertTrue(h._history, "Der Verlauf darf beim Abbruch nicht wegfallen")


if __name__ == "__main__":
    unittest.main()
