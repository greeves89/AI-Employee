"""Ein Agent muss seine MCP-Verdrahtung NACHSEHEN koennen, nicht erraten.

Am 22.09.2026 meldete ein Agent dem Nutzer, Orchestrator, Skills und Memory
seien "in dieser Session nicht verbunden (Connection-Fehler)". Nachgemessen
war das falsch: Alle zehn Server antworteten, und im Protokoll stand zur
fraglichen Zeit kein einziger MCP-Fehler. Der Agent hatte die
Diagnosewarnungen von ``claude mcp list`` gelesen -- doppelte Eintraege,
uebersprungene Adressen -- und daraus einen Ausfall gefolgert.

Das war kein Missgeschick des Modells, sondern eine Luecke: Es gab keine
Stelle, an der ein Agent eine klare Antwort auf "laufen meine Werkzeuge?"
bekommt. Warntexte ueber die Konfiguration sagen darueber nichts aus -- ein
Server kann doppelt eingetragen sein und trotzdem einwandfrei antworten.

``/diag`` liefert diese Antwort jetzt: gemessen am laufenden Prozess, nicht
an der Konfiguration.
"""

import json
import os
import unittest
from unittest import mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app import health


class DiagMcpTest(unittest.IsolatedAsyncioTestCase):

    async def _antwort(self) -> tuple[int, dict]:
        app = web.Application()
        app["agent_id"] = "a1"
        app.router.add_get("/diag", health.diag_handler)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            r = await client.get("/diag")
            return r.status, await r.json()
        finally:
            await client.close()

    async def test_ohne_gemeinsamen_prozess_kein_fehlalarm(self):
        """Einzelprozess-Betrieb ist kein Fehler, nur ein anderer Modus."""
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": ""}, clear=False):
            status, body = await self._antwort()
        self.assertEqual(body["checks"]["mcp_server"], "ok")
        self.assertEqual(body["mcp"]["modus"], "einzelprozesse")
        self.assertEqual(status, 200)

    async def test_toter_mcp_prozess_wird_als_fehler_gemeldet(self):
        """Der Fall, den der Agent nur raten konnte."""
        # Port, auf dem nichts horcht.
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": "9"}, clear=False):
            status, body = await self._antwort()
        self.assertEqual(body["checks"]["mcp_server"], "FAILED", body)
        self.assertGreater(len(body["mcp"]["fehlend"]), 0)
        self.assertEqual(status, 500, "Ein Waechter muss darauf anschlagen koennen.")

    async def test_laufender_mcp_prozess_gilt_als_in_ordnung(self):
        """Gegenprobe an einem echten, horchenden Port."""
        hilf = web.Application()
        hilf.router.add_get("/", lambda _r: web.Response(text="ok"))
        server = TestServer(hilf)
        await server.start_server()
        try:
            with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": str(server.port)}, clear=False):
                status, body = await self._antwort()
            self.assertEqual(body["checks"]["mcp_server"], "ok", body)
            self.assertEqual(body["mcp"]["fehlend"], [])
            self.assertGreaterEqual(body["mcp"]["erreichbar"], 6)
            self.assertEqual(status, 200)
        finally:
            await server.close()

    async def test_die_alte_pruefung_bleibt_erhalten(self):
        """Der Arg-Parse-Selbsttest (#342) darf dabei nicht verloren gehen."""
        with mock.patch.dict(os.environ, {"MCP_HTTP_PORT": ""}, clear=False):
            _, body = await self._antwort()
        self.assertIn("mcp_arg_parse", body["checks"])




class WerkzeugZustandCustomLlmTests(unittest.TestCase):
    """#161: eingetragener Server ohne Werkzeuge muss sichtbar sein."""

    def _zustand(self, modus, server):
        from app import health
        from app.config import settings

        katalog = [{"function": {"name": "bash"}}, {"function": {"name": "mcp_msgraph_ms_search"}}]
        with mock.patch.object(settings, "agent_mode", modus), \
                mock.patch("app.tools.definitions.TOOL_DEFINITIONS", katalog), \
                mock.patch.dict(os.environ, {"CUSTOM_MCP_SERVERS": server}):
            return health._werkzeug_zustand()

    def test_cli_laufzeit_meldet_nichts(self):
        self.assertIsNone(self._zustand("claude_code", "{}"))

    def test_server_ohne_werkzeuge_wird_genannt(self):
        zustand = self._zustand("custom_llm", '{"msgraph": "http://a", "email": "http://b"}')
        self.assertEqual(zustand["server_ohne_werkzeuge"], ["email"])
        self.assertEqual(zustand["anzahl"], 2)

    def test_kaputter_eintrag_wirft_nicht(self):
        self.assertEqual(self._zustand("custom_llm", "kein json")["mcp_server"], [])


if __name__ == "__main__":
    unittest.main()
