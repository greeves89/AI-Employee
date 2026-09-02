"""Die redirect_uri des Token-Tauschs muss die der Anfrage sein, nicht die aktuelle.

RFC 6749 4.1.3 verlangt in beiden Schritten denselben Wert. Solange `oauth_connect`
und `oauth_callback` ihn je frisch aus `oauth_callback_base_url` berechnen, haelt das
nur, wenn niemand diesen Wert waehrend des Browser-Umwegs aendert. Genau dafuer ist
das Feld aber da: ein Administrator stellt es ein, weil der Anbieter eine enge
Allowlist hat — und stellt es notfalls waehrend eines Verbindungsversuchs um.

Dann schlaegt der Tausch mit einem nichtssagenden `invalid_grant` fehl, obwohl Code
und PKCE-Verifier stimmen. Deshalb wird der Wert beim Start festgehalten.

Geprueft wird die FAEHIGKEIT (welcher Wert geht an den Anbieter), nicht der Name des
Feldes im Zustand.
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import mcp_servers
from app.models.mcp_server import McpServer

BASIS_ALT = "https://alt.example.test"
BASIS_NEU = "https://neu.example.test"
ERWARTET_ALT = f"{BASIS_ALT}/api/v1/mcp-servers/oauth/callback"


def _server() -> McpServer:
    s = McpServer()
    s.id = 7
    s.name = "oauth-mcp"
    s.oauth_enabled = True
    s.oauth_authorization_endpoint = "https://idp.example.test/authorize"
    s.oauth_token_endpoint = "https://idp.example.test/token"
    s.oauth_client_id = "client-abc"
    s.oauth_client_secret_encrypted = None
    s.oauth_scope = "read"
    s.oauth_resource = None
    s.oauth_callback_base_url = BASIS_ALT
    return s


def _redis_mit_speicher(speicher: dict) -> MagicMock:
    async def setex(key, ttl, wert):
        speicher[key] = wert

    async def get(key):
        return speicher.get(key)

    redis = MagicMock()
    redis.client = MagicMock()
    redis.client.setex = AsyncMock(side_effect=setex)
    redis.client.get = AsyncMock(side_effect=get)
    redis.client.delete = AsyncMock()
    return redis


class RedirectUriBleibtFestgenagelt(unittest.IsolatedAsyncioTestCase):
    async def test_tausch_benutzt_die_uri_der_anfrage_nicht_die_aktuelle(self):
        server = _server()
        speicher: dict = {}
        redis = _redis_mit_speicher(speicher)
        db = AsyncMock()
        db.get = AsyncMock(return_value=server)

        antwort = await mcp_servers.oauth_connect(
            server_id=7, user=MagicMock(id="u1"), db=db, redis=redis
        )
        self.assertIn(ERWARTET_ALT.replace(":", "%3A").replace("/", "%2F"),
                      antwort["authorization_url"].replace("%3a", "%3A"))

        # Der Administrator stellt die Basis waehrend des Browser-Umwegs um.
        server.oauth_callback_base_url = BASIS_NEU

        (state,) = [k.rsplit(":", 1)[-1] for k in speicher]
        request = MagicMock()
        request.query_params = {"code": "auth-code", "state": state}

        with patch.object(mcp_servers, "_integrations_redirect", return_value="ok"), \
             patch("app.services.mcp_oauth_refresh.perform_token_request",
                   new=AsyncMock(return_value={"access_token": "at"})), \
             patch("app.services.mcp_oauth_refresh.apply_token_to_server"), \
             patch("app.services.mcp_oauth_client.build_token_exchange_data") as tausch:
            tausch.return_value = {}
            await mcp_servers.oauth_callback(request=request, db=db, redis=redis)

        self.assertEqual(tausch.call_args.kwargs["redirect_uri"], ERWARTET_ALT)

    async def test_alter_zustand_ohne_feld_faellt_auf_die_berechnung_zurueck(self):
        """Zustaende aus der Zeit vor dieser Aenderung leben bis zu 10 Minuten weiter."""
        server = _server()
        speicher = {
            mcp_servers._STATE_PREFIX + "s1": json.dumps(
                {"server_id": 7, "code_verifier": "v", "user_id": "u1"}
            )
        }
        redis = _redis_mit_speicher(speicher)
        db = AsyncMock()
        db.get = AsyncMock(return_value=server)

        request = MagicMock()
        request.query_params = {"code": "auth-code", "state": "s1"}

        with patch.object(mcp_servers, "_integrations_redirect", return_value="ok"), \
             patch("app.services.mcp_oauth_refresh.perform_token_request",
                   new=AsyncMock(return_value={"access_token": "at"})), \
             patch("app.services.mcp_oauth_refresh.apply_token_to_server"), \
             patch("app.services.mcp_oauth_client.build_token_exchange_data") as tausch:
            tausch.return_value = {}
            await mcp_servers.oauth_callback(request=request, db=db, redis=redis)

        self.assertEqual(tausch.call_args.kwargs["redirect_uri"], ERWARTET_ALT)


if __name__ == "__main__":
    unittest.main()
