"""Wer schreibt, waehrend der Agent arbeitet, erfaehrt sofort, was passiert.

29.09.2026: Nachricht in einem neuen Chat, gut zwei Minuten kein Zeichen. Der
Agent arbeitete an einem anderen Gespraech und einer Aufgabe; das Signal
„queued" behauptete immer „steering current agent turn" — auch wenn die
Nachricht in Wahrheit wartete.
"""
import json
import unittest
from unittest.mock import patch

from app.api import ws


class _Db:
    """Jede Sitzung liest aus DERSELBEN Antwortliste weiter (Name, dann Titel)."""

    def __init__(self, antworten):
        self.antworten = antworten

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def scalar(self, stmt):
        return self.antworten.pop(0) if self.antworten else None


class WarteHinweisTests(unittest.IsolatedAsyncioTestCase):
    async def hinweis(self, status, faden, antworten):
        antworten = list(antworten)
        with patch.object(ws, "async_session_factory", lambda: _Db(antworten)):
            return await ws._warte_hinweis("a1", status, faden)

    async def test_dieses_gespraech_heisst_einfliessen(self):
        h = await self.hinweis({"current_task": "chat:s1", "active_sessions": json.dumps(["chat:s1"])}, "s1", ["Ben"])
        self.assertTrue(h["steering"])
        self.assertIn("Ben nimmt sie", h["message"])

    async def test_anderes_gespraech_heisst_warten(self):
        h = await self.hinweis({"current_task": "chat:s9", "active_sessions": json.dumps(["chat:s9"])}, "s1", ["Ben"])
        self.assertFalse(h["steering"])
        self.assertEqual(h["message"], "Ben arbeitet gerade an einem anderen Gespräch — deine Nachricht ist als Nächstes dran.")

    async def test_aufgabe_wird_beim_namen_genannt(self):
        h = await self.hinweis({"current_task": "t1", "active_sessions": json.dumps(["t1"])}, "s1",
                               ["Ben", "[Manual] Checkliste Kundengespräch"])
        self.assertFalse(h["steering"])
        self.assertIn("„Checkliste Kundengespräch“", h["message"])




class WartendeGespraecheTests(unittest.IsolatedAsyncioTestCase):
    """Der Status meldet Faeden mit unbeantworteten Nachrichten — aus der
    Warteschlange und aus der Liste der abgeholten, noch laufenden."""

    async def test_warteschlange_und_in_arbeit(self):
        from types import SimpleNamespace

        from app.core.agent_manager import AgentManager

        listen = {
            "agent:a1:chat": [json.dumps({"chat_session_id": "s2"}), "kaputt"],
            "agent:a1:chat:inflight": [json.dumps({"chat_session_id": "s1"}), json.dumps({"chat_session_id": "s2"})],
        }

        class _Redis:
            async def lrange(self, key, a, b):
                return listen.get(key, [])

        verwalter = AgentManager.__new__(AgentManager)
        verwalter.redis = SimpleNamespace(client=_Redis())
        self.assertEqual(await verwalter._wartende_gespraeche("a1"), ["s2", "s1"])


if __name__ == "__main__":
    unittest.main()
