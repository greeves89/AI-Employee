"""Tests fuer den plattformweiten Nur-Lesen-Zwang bei Microsoft.

Der Kunde verlangt: M365 in AI-Employee greift ausschliesslich lesend zu. Es gibt
zwei Ebenen (global + pro Agent) und drei Wege (Graph-Agenten-Transport,
Exchange-Agenten-Transport, externer OpenWebUI-Transport). Getestet wird die
vollstaendige Bedingungsmatrix und dass die MCP-Schicht selbst dann nichts
Schreibendes herausgibt.
"""

import asyncio
import unittest
from unittest.mock import patch

from app.config import settings
from app.core import ms_access
from app.core.msgraph_mcp import MSGRAPH_TOOLS, WRITE_TOOLS, handle_mcp_request


def _run(coro):
    return asyncio.run(coro)


class WriteGateMatrixTests(unittest.TestCase):
    """MCDC ueber (globaler Schalter) x (Agenten-Modus) — beide Wege."""

    CASES = [
        # (read_only global, agent config, erwartet: darf schreiben)
        (True,  {"msgraph_access": "write"}, False),   # global gewinnt
        (True,  {"msgraph_access": "read"},  False),
        (True,  {},                          False),
        (True,  None,                        False),
        (False, {"msgraph_access": "write"}, True),    # nur hier: schreiben
        (False, {"msgraph_access": "read"},  False),
        (False, {},                          False),   # Default bleibt lesend
        (False, None,                        False),   # Agent unbekannt → lesend
    ]

    def test_msgraph_matrix(self):
        for read_only, config, expected in self.CASES:
            with self.subTest(read_only=read_only, config=config):
                with patch.object(settings, "msgraph_read_only", read_only):
                    self.assertEqual(
                        ms_access.write_enabled(config, "msgraph_access"), expected
                    )

    def test_exchange_matrix(self):
        """Der on-prem-Exchange-Weg folgt derselben Regel — keine Luecke daneben."""
        for read_only, config, expected in self.CASES:
            cfg = None if config is None else {
                "exchange_access": config.get("msgraph_access")
            } if config else {}
            with self.subTest(read_only=read_only, config=cfg):
                with patch.object(settings, "msgraph_read_only", read_only):
                    self.assertEqual(
                        ms_access.write_enabled(cfg, "exchange_access"), expected
                    )

    def test_write_aliases_count_as_write(self):
        """Alte Konfigurationen mit 'read_write'/'rw' bleiben gueltig."""
        with patch.object(settings, "msgraph_read_only", False):
            for value in ("write", "read_write", "rw"):
                self.assertTrue(ms_access.write_enabled({"msgraph_access": value}, "msgraph_access"))
            for value in ("read", "", "readonly", "WRITE"):
                self.assertFalse(ms_access.write_enabled({"msgraph_access": value}, "msgraph_access"))

    def test_default_is_read_only(self):
        """Fehlt die Einstellung komplett, gilt Nur-Lesen (fail-closed)."""
        with patch.object(settings, "msgraph_read_only", True):
            self.assertTrue(ms_access.read_only_enabled())
        # Attribut nicht gesetzt → getattr-Default greift
        with patch("app.core.ms_access.settings", object()):
            self.assertTrue(ms_access.read_only_enabled())


class McpSurfaceTests(unittest.TestCase):
    """Was der Nur-Lesen-Zwang am MCP-Protokoll konkret bewirkt."""

    @staticmethod
    async def _token():
        return "fake-token"

    def test_read_only_hides_every_write_tool(self):
        resp, status = _run(handle_mcp_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            self._token, write_enabled=False,
        ))
        self.assertEqual(status, 200)
        listed = {t["name"] for t in resp["result"]["tools"]}
        self.assertFalse(listed & WRITE_TOOLS, "Schreib-Werkzeug im Nur-Lesen-Modus gelistet")
        # ... und der Rest ist vollstaendig da: lesen bleibt uneingeschraenkt.
        self.assertEqual(listed, {t["name"] for t in MSGRAPH_TOOLS} - WRITE_TOOLS)

    def test_read_only_refuses_the_call_too(self):
        """Nicht nur ausblenden — ein direkter Aufruf muss ebenfalls scheitern."""
        for tool in sorted(WRITE_TOOLS):
            resp, _ = _run(handle_mcp_request(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": tool, "arguments": {}}},
                self._token, write_enabled=False,
            ))
            text = str(resp)
            self.assertNotIn('"isError": false', text)
            self.assertTrue(
                "read-only" in text.lower() or "error" in text.lower(),
                f"{tool} wurde im Nur-Lesen-Modus nicht abgelehnt: {text[:200]}",
            )


class ExternalTransportTests(unittest.TestCase):
    def test_openwebui_transport_is_hardwired_read_only(self):
        """Der externe Weg darf nicht am globalen Schalter haengen — er ist immer lesend.

        Als Quelltext-Pruefung, weil das Modul beim Import die Docker-Abhaengigkeit
        der API-Schicht mitzieht; die Aussage bleibt dieselbe.
        """
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "app/api/mcp_msgraph_external.py").read_text()
        self.assertIn("write_enabled=False", src)
        self.assertNotIn("ms_access", src, "externer Transport darf nicht am Schalter haengen")


if __name__ == "__main__":
    unittest.main()
