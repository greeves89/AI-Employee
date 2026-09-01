"""Wer die Callback-Basis umstellt, muss den Client neu registrieren koennen.

Bei dynamischer Registrierung (RFC 7591) haengt der `client_id` am `redirect_uri`,
gegen den er beim Anbieter angelegt wurde. Aendert ein Administrator die Basis, zeigt
der gespeicherte `client_id` weiterhin auf die alte Adresse — der naechste Versuch
scheitert mit `invalid_request`.

Der Weg zurueck war versperrt: `oauth_discover` registriert nur neu, solange kein
`client_id` gespeichert ist, und ueber die API liess sich der alte nicht loeschen.
Darum wird er beim Wechsel der Basis verworfen.

Geprueft wird die FAEHIGKEIT — kommt der Administrator nach dem Wechsel wieder zu
einer gueltigen Registrierung — nicht das Setzen eines einzelnen Feldes.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import mcp_servers
from app.models.mcp_server import McpServer

BASIS_ALT = "https://alt.example.test"
BASIS_NEU = "https://neu.example.test"
ERWARTET_NEU = f"{BASIS_NEU}/api/v1/mcp-servers/oauth/callback"


def _server() -> McpServer:
    s = McpServer()
    s.id = 7
    s.name = "oauth-mcp"
    s.url = "https://mcp.example.test/sse"
    s.oauth_enabled = True
    s.oauth_client_id = "dcr-client-abc"
    s.oauth_client_secret_encrypted = "verschluesselt"
    s.oauth_callback_base_url = BASIS_ALT
    return s


def _db(server: McpServer) -> AsyncMock:
    treffer = MagicMock()
    treffer.scalar_one_or_none = MagicMock(return_value=server)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=treffer)
    db.get = AsyncMock(return_value=server)
    return db


async def _patch(server: McpServer, **felder):
    body = mcp_servers.McpServerUpdate(**felder)
    return await mcp_servers.update_mcp_server(
        server_id=7, body=body, user=MagicMock(id="u1"), db=_db(server)
    )


class ClientIdFolgtDerCallbackBasis(unittest.IsolatedAsyncioTestCase):
    async def test_neue_basis_verwirft_die_alte_registrierung(self):
        server = _server()
        await _patch(server, oauth_callback_base_url=BASIS_NEU)
        self.assertIsNone(server.oauth_client_id)
        self.assertIsNone(server.oauth_client_secret_encrypted)

    async def test_gleiche_basis_laesst_die_registrierung_stehen(self):
        server = _server()
        await _patch(server, oauth_callback_base_url=BASIS_ALT)
        self.assertEqual(server.oauth_client_id, "dcr-client-abc")
        self.assertEqual(server.oauth_client_secret_encrypted, "verschluesselt")

    async def test_anderes_feld_laesst_die_registrierung_stehen(self):
        server = _server()
        await _patch(server, name="umbenannt")
        self.assertEqual(server.oauth_client_id, "dcr-client-abc")
        self.assertEqual(server.oauth_callback_base_url, BASIS_ALT)

    async def test_nach_dem_wechsel_registriert_discover_gegen_die_neue_uri(self):
        """Die eigentliche Faehigkeit: der Administrator kommt wieder heraus."""
        server = _server()
        await _patch(server, oauth_callback_base_url=BASIS_NEU)

        endpunkte = {
            "authorization_endpoint": "https://idp.example.test/authorize",
            "token_endpoint": "https://idp.example.test/token",
            "registration_endpoint": "https://idp.example.test/register",
        }
        registrierung = AsyncMock(return_value={"client_id": "dcr-client-neu"})

        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value="Bearer x")), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=AsyncMock(return_value={"issuer": "https://idp.example.test"})), \
             patch.object(mcp_servers, "_register_oauth_client", new=registrierung), \
             patch("app.services.mcp_oauth_client.resource_metadata_url", return_value="https://idp.example.test/prm"), \
             patch("app.services.mcp_oauth_client.pick_authorization_server", return_value="https://idp.example.test"), \
             patch("app.services.mcp_oauth_client.as_metadata_urls", return_value=["https://idp.example.test/meta"]), \
             patch("app.services.mcp_oauth_client.select_endpoints", return_value=endpunkte), \
             patch("app.services.mcp_oauth_client.default_scope", return_value="read"):
            antwort = await mcp_servers.oauth_discover(
                server_id=7, body=None, user=MagicMock(id="u1"), db=_db(server)
            )

        self.assertEqual(registrierung.await_args.args[1], ERWARTET_NEU)
        self.assertTrue(antwort["dynamically_registered"])
        self.assertEqual(antwort["redirect_uri"], ERWARTET_NEU)


if __name__ == "__main__":
    unittest.main()
