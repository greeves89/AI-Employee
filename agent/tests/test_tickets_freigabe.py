"""Ticket-Werkzeug bei „Freigabe“ in der Rechte-Matrix (core/gebundene_freigabe).

Der Server antwortet dann mit 202 und legt das Ticket erst nach der Freigabe selbst
an. Das Werkzeug darf das nicht als „Ticket created“ melden — sonst hielte der Agent
die Sache für erledigt.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.tools.api_client import OrchestratorAPIClient

WARTET = {"status": "wartet_auf_freigabe", "approval_id": "42",
          "hinweis": "Nicht ausgeführt: „Ticket“ wartet auf Freigabe."}


class TicketFreigabeTests(unittest.IsolatedAsyncioTestCase):
    async def test_anlegen_meldet_die_freigabe(self):
        client = OrchestratorAPIClient.__new__(OrchestratorAPIClient)
        with patch.object(OrchestratorAPIClient, "_request", AsyncMock(return_value=WARTET)):
            text = await client.tickets({"action": "create", "title": "Drucker"})
        self.assertIn("wartet auf Freigabe", text)
        self.assertNotIn("created", text)

    async def test_kommentar_meldet_die_freigabe(self):
        client = OrchestratorAPIClient.__new__(OrchestratorAPIClient)
        with patch.object(OrchestratorAPIClient, "_request", AsyncMock(return_value=WARTET)):
            text = await client.tickets({"action": "comment", "ticket_id": "T-1", "text": "Erledigt"})
        self.assertIn("wartet auf Freigabe", text)

    def test_mcp_werkzeug_meldet_die_freigabe_ebenso(self):
        """Parität: Claude Code und Codex nutzen das MCP-Werkzeug."""
        from pathlib import Path
        quelle = (Path(__file__).resolve().parents[1] / "mcp" / "orchestrator-server.mjs").read_text()
        self.assertEqual(quelle.count('status === "wartet_auf_freigabe"'), 2)


if __name__ == "__main__":
    unittest.main()
