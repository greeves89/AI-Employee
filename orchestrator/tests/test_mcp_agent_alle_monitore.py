"""Orchestrator-MCP (computer_use): ohne ``display`` sieht der Agent jeden Monitor.

Die Bridge wird nachgestellt: ihr WebSocket beantwortet den Befehl sofort. Geprüft
wird, dass der Wunsch ``alle_bildschirme`` mitgeht, und dass jedes Bild mit seiner
Monitornummer beim Agenten ankommt — sonst klickt er auf dem falschen.
"""

import json
import unittest
from types import SimpleNamespace

from app.api import computer_use, mcp_agent


class _Bridge:
    def __init__(self, sitzung, antwort):
        self.sitzung, self.antwort, self.gesendet = sitzung, antwort, []

    async def send_text(self, text):
        msg = json.loads(text)
        self.gesendet.append(msg["command"])
        self.sitzung["pending_results"][msg["id"]].set_result(self.antwort)


class AlleMonitore(unittest.IsolatedAsyncioTestCase):
    async def _aufruf(self, antwort, params=None):
        sitzung = {"user_id": "u1", "agent_id": None, "bridge_connected": True,
                   "allowed_capabilities": computer_use.DEFAULT_ALLOWED_CAPABILITIES}
        sitzung["bridge_ws"] = bridge = _Bridge(sitzung, antwort)
        computer_use._sessions["s-test"] = sitzung
        self.addCleanup(computer_use._sessions.pop, "s-test", None)
        agent = SimpleNamespace(id="a1", user_id="u1")
        args = {"action": "screenshot", "session_id": "s-test"}
        if params is not None:
            args["params"] = params
        ergebnis = await mcp_agent._call_tool("computer_use", args, agent, None, None)
        return ergebnis, bridge.gesendet[0]

    async def test_ohne_display_alle_bilder_mit_nummer(self):
        ergebnis, befehl = await self._aufruf({
            "screenshot_b64": "haupt", "display": 1,
            "weitere": [{"display": 2, "screenshot_b64": "zweiter"}],
        })
        self.assertEqual(befehl["params"], {"alle_bildschirme": True})
        inhalt = ergebnis["content"]
        self.assertEqual([b["data"] for b in inhalt if b["type"] == "image"], ["haupt", "zweiter"])
        texte = [b["text"] for b in inhalt if b["type"] == "text"]
        self.assertIn("Display 2:", texte)
        self.assertTrue(any("params.display=N" in t for t in texte))

    async def test_mit_display_ein_bild_wie_bisher(self):
        ergebnis, befehl = await self._aufruf({"screenshot_b64": "zwei", "display": 2}, {"display": 2})
        self.assertEqual(befehl["params"], {"display": 2})
        self.assertEqual(ergebnis["content"], [{"type": "image", "data": "zwei", "mimeType": "image/png"}])


if __name__ == "__main__":
    unittest.main()
