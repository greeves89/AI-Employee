"""Teams-Antworten im Kanal respektieren den Nur-Lesen-Schalter für Microsoft 365.

Bis 1.444 ging ``teams_gateway.send_reply`` am plattformweiten Schalter vorbei (Voreinstellung:
an): Ein Admin, der „nur lesend“ eingestellt hatte, sah trotzdem Nachrichten unter dem
Namen des Agenten-Besitzers in Teams.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import teams_gateway


class NurLesenTests(unittest.IsolatedAsyncioTestCase):

    async def _senden(self, nur_lesen: bool):
        graph = AsyncMock()
        with patch("app.core.ms_access.read_only_enabled", return_value=nur_lesen), \
                patch.object(teams_gateway, "_graph_token", AsyncMock(return_value="tok")), \
                patch("app.core.msgraph_mcp._graph", graph):
            ok = await teams_gateway.send_reply(SimpleNamespace(user_id="u1", id="a1"),
                                                {"chat_id": "19:abc"}, "Hallo")
        return ok, graph

    async def test_nur_lesen_sendet_nicht(self):
        ok, graph = await self._senden(True)
        self.assertFalse(ok)
        graph.assert_not_awaited()

    async def test_schreiben_erlaubt_sendet_wie_bisher(self):
        ok, graph = await self._senden(False)
        self.assertTrue(ok)
        graph.assert_awaited_once()
        self.assertEqual(graph.await_args.args[:2], ("POST", "/chats/19:abc/messages"))


if __name__ == "__main__":
    unittest.main()
