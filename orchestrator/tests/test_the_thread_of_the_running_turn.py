"""Welchen Gespraechsfaden der Orchestrator einem Auftrag ohne Faden zuordnet.

Auftraege aus dem stdio-MCP-Server (Claude Code, Codex) tragen keinen Faden mit —
der Werkzeugserver kennt die Sitzung des Chats nicht. ``_session_of_running_turn``
ist der Auffangweg: er liest den Statuseintrag des Agenten und schaut, in welchem
Gespraech dieser Agent GERADE arbeitet.

Zwei Faelle sind dabei die eigentliche Lehre, und beide stammen aus dem Vorfall
vom 2026-08-13 (vier Auftraege in derselben Sekunde, zwei Kacheln verloren):

* ``current_task`` traegt nur EINE Arbeit. Wer nebenher einen Zeitplan-Auftrag
  abarbeitet, hat dort dessen Kennung stehen — der Chat war unsichtbar. Deshalb
  wird ``active_sessions`` mitgelesen.
* Bei mehreren offenen Gespraechen waere jede Wahl geraten. Eine Kachel im
  falschen Gespraech ist schlimmer als keine, also wird bewusst nichts geliefert.

Vorher stand dieselbe Zusicherung als Textsuche in einem 2200-Zeichen-Fenster auf
dem Quelltext dieser Datei (``agent/tests/test_every_task_carries_its_thread.py``).
Das Fenster hatte 27 Zeichen Luft: ein eingefuegter Kommentar in ``task_router.py``
haette die Hauptlinie rot gefaerbt, ohne dass am Verhalten etwas falsch war. Ein
Zeichenfenster misst Abstand, gemeint war Reihenfolge — siehe Issue #726. Hier
wird stattdessen der echte Ablauf gefahren.
"""

import json
import unittest

from app.core.task_router import TaskRouter


class _Client:
    """Nur ``hgetall`` — genau das, was der Auffangweg von Redis benutzt."""

    def __init__(self, status: dict):
        self._status = status
        self.gefragt: list[str] = []

    async def hgetall(self, key: str) -> dict:
        self.gefragt.append(key)
        return dict(self._status)


class _Redis:
    def __init__(self, status: dict):
        self.client = _Client(status)


def _router(status: dict) -> TaskRouter:
    """Ein Router ohne ``__init__`` — der Auffangweg braucht nur ``self.redis``."""
    router = TaskRouter.__new__(TaskRouter)
    router.redis = _Redis(status)
    return router


class TheRunningTurnDecidesTheThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_thread_of_the_current_turn_is_used(self):
        router = _router({"current_task": "chat:93340239600f"})

        self.assertEqual(
            await router._session_of_running_turn("a1"), "93340239600f"
        )

    async def test_it_asks_for_the_status_of_that_very_agent(self):
        router = _router({"current_task": "chat:abc"})

        await router._session_of_running_turn("a1")

        self.assertEqual(router.redis.client.gefragt, ["agent:a1:status"])

    async def test_it_also_looks_at_all_running_work(self):
        """``current_task`` traegt die Zeitplan-Arbeit — der Chat steht nur in
        ``active_sessions``. Genau hier gingen 2026-08-13 die Kacheln verloren."""
        router = _router({
            "current_task": "task:tabc123",
            "active_sessions": json.dumps(["task:tabc123", "chat:cafe1234"]),
        })

        self.assertEqual(
            await router._session_of_running_turn("a1"), "cafe1234"
        )

    async def test_it_refuses_to_guess_between_several_chats(self):
        """Eine Kachel im falschen Gespraech ist schlimmer als keine.

        Gegenstueck zum Test darueber: dieselbe Lage, nur mit einem ZWEITEN
        offenen Gespraech — und schon darf nichts mehr geliefert werden.
        """
        router = _router({
            "active_sessions": json.dumps(["chat:cafe1234", "chat:beef5678"]),
        })

        self.assertIsNone(await router._session_of_running_turn("a1"))

    async def test_a_single_chat_among_other_work_is_still_unambiguous(self):
        """Gezaehlt werden die GESPRAECHE, nicht die laufenden Arbeiten."""
        router = _router({
            "active_sessions": json.dumps(
                ["task:t1", "task:t2", "chat:cafe1234"]
            ),
        })

        self.assertEqual(
            await router._session_of_running_turn("a1"), "cafe1234"
        )


class TheFallbackStaysQuietWhenItCannotKnowTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_an_agent_nothing_is_guessed(self):
        """Der Status traegt hier absichtlich einen Faden: ohne Agenten darf er
        trotzdem nicht herauskommen, sonst erbt ein Auftrag ohne Absender den
        Chat des zuletzt Befragten."""
        router = _router({"current_task": "chat:93340239600f"})

        self.assertIsNone(await router._session_of_running_turn(None))

    async def test_without_redis_nothing_is_guessed(self):
        router = TaskRouter.__new__(TaskRouter)
        router.redis = None

        self.assertIsNone(await router._session_of_running_turn("a1"))

    async def test_an_empty_status_yields_nothing(self):
        self.assertIsNone(await _router({})._session_of_running_turn("a1"))

    async def test_work_without_any_chat_yields_nothing(self):
        router = _router({
            "current_task": "task:tabc123",
            "active_sessions": json.dumps(["task:tabc123"]),
        })

        self.assertIsNone(await router._session_of_running_turn("a1"))

    async def test_a_broken_list_is_not_a_crash(self):
        """Der Auffangweg laeuft im Zustellpfad eines Auftrags — er darf ihn
        unter keinen Umstaenden mitreissen."""
        router = _router({"active_sessions": "{kein json"})

        self.assertIsNone(await router._session_of_running_turn("a1"))

    async def test_bytes_from_redis_are_understood(self):
        """Ohne ``decode_responses`` liefert Redis Bytes — dann darf der Faden
        nicht still verlorengehen."""
        router = _router({b"current_task": b"chat:93340239600f"})

        self.assertEqual(
            await router._session_of_running_turn("a1"), "93340239600f"
        )


if __name__ == "__main__":
    unittest.main()
