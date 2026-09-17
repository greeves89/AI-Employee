"""Issue #729: adding an OAuth-protected MCP server failed when the server
advertises Protected Resource Metadata ONLY via the well-known URIs, without a
``WWW-Authenticate`` header on the 401.

Reproduction from the issue: ``https://mcp.ws.sonos.com/mcp`` answers 401 with
no ``WWW-Authenticate`` header at all, but serves valid PRM at
``/.well-known/oauth-protected-resource/mcp`` (and at the root). The MCP spec
(2025-11-25) requires clients to fall back to these well-known URIs — path
first, then root — when the header is absent. Before this fix, ``add_mcp_server``
never tried, so the add aborted as a rejected static token and no row was
created, leaving the Connect flow unreachable.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import mcp_servers
from app.models.mcp_server import McpServer
from fastapi import HTTPException

SONOS_URL = "https://mcp.ws.sonos.com/mcp"
PATH_PRM_URL = "https://mcp.ws.sonos.com/.well-known/oauth-protected-resource/mcp"
ROOT_PRM_URL = "https://mcp.ws.sonos.com/.well-known/oauth-protected-resource"

_VALID_PRM = {
    "resource": SONOS_URL,
    "authorization_servers": ["https://auth.sonos.com"],
}


def _fetch_json_that_only_answers(url_to_doc: dict):
    """Fake _oauth_fetch_json: 400s (as the real helper would) for any URL not
    in the map — matching how a genuine 404 well-known path behaves."""
    async def _fetch(url):
        if url in url_to_doc:
            return url_to_doc[url]
        raise HTTPException(status_code=400, detail=f"Discovery document {url} returned 404")
    return _fetch


class AdvertisesOAuthWithAHeaderTests(unittest.IsolatedAsyncioTestCase):
    """Regression check: the pre-existing header-based path must keep working
    exactly as before — no fetch, just trusts the challenge's own pointer."""

    async def test_a_challenge_with_a_resource_metadata_pointer_counts(self):
        with patch.object(mcp_servers, "_oauth_probe_challenge",
                          new=AsyncMock(return_value='Bearer resource_metadata="https://x/prm"')):
            self.assertTrue(await mcp_servers._advertises_oauth(SONOS_URL))

    async def test_a_bearer_challenge_without_the_pointer_does_not_count(self):
        with patch.object(mcp_servers, "_oauth_probe_challenge",
                          new=AsyncMock(return_value="Bearer realm=\"x\"")):
            self.assertFalse(await mcp_servers._advertises_oauth(SONOS_URL))


class AdvertisesOAuthWithoutAHeaderTests(unittest.IsolatedAsyncioTestCase):
    """The new fallback (#729): no WWW-Authenticate at all."""

    async def test_valid_prm_at_the_path_well_known_uri_counts(self):
        fetch = _fetch_json_that_only_answers({PATH_PRM_URL: _VALID_PRM})
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch):
            self.assertTrue(await mcp_servers._advertises_oauth(SONOS_URL))

    async def test_valid_prm_only_at_the_root_well_known_uri_still_counts(self):
        """The Sonos case exactly: the path variant 404s, root has it."""
        fetch = _fetch_json_that_only_answers({ROOT_PRM_URL: _VALID_PRM})
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch):
            self.assertTrue(await mcp_servers._advertises_oauth(SONOS_URL))

    async def test_neither_well_known_uri_answering_is_a_plain_rejected_token(self):
        fetch = _fetch_json_that_only_answers({})
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch):
            self.assertFalse(await mcp_servers._advertises_oauth(SONOS_URL))

    async def test_a_document_with_no_authorization_servers_does_not_count(self):
        doc = {"resource": SONOS_URL, "authorization_servers": []}
        fetch = _fetch_json_that_only_answers({PATH_PRM_URL: doc})
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch):
            self.assertFalse(await mcp_servers._advertises_oauth(SONOS_URL))

    async def test_a_document_for_an_unrelated_resource_does_not_count(self):
        """A host that happens to serve SOMETHING at the well-known path for a
        different resource must not be misread as OAuth for THIS resource —
        the guard against misreading a plain rejected static token."""
        unrelated = {"resource": "https://other-host.test/mcp",
                     "authorization_servers": ["https://auth.other.test"]}
        fetch = _fetch_json_that_only_answers({PATH_PRM_URL: unrelated})
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch):
            self.assertFalse(await mcp_servers._advertises_oauth(SONOS_URL))

    async def test_a_probe_failure_still_returns_false_not_raises(self):
        with patch.object(mcp_servers, "_oauth_probe_challenge",
                          new=AsyncMock(side_effect=HTTPException(status_code=400, detail="unreachable"))):
            self.assertFalse(await mcp_servers._advertises_oauth(SONOS_URL))


def _server(url=SONOS_URL) -> McpServer:
    s = McpServer()
    s.id = 9
    s.name = "sonos"
    s.url = url
    s.oauth_enabled = False
    s.oauth_client_id = None
    return s


def _db(server: McpServer) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=server)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


class OAuthDiscoverWithoutAHeaderTests(unittest.IsolatedAsyncioTestCase):
    """The 'optionally let oauth_discover try the root variant too' half of
    #729: without this, a server added via the new well-known fallback above
    could never actually complete the Connect flow — the add would succeed
    but clicking 'Verbinden' would still 400."""

    async def test_discover_falls_back_to_the_root_well_known_uri(self):
        server = _server()
        endpunkte = {
            "authorization_endpoint": "https://auth.sonos.com/authorize",
            "token_endpoint": "https://auth.sonos.com/token",
            "registration_endpoint": None,
        }
        as_meta = {"issuer": "https://auth.sonos.com"}
        fetch = _fetch_json_that_only_answers({
            ROOT_PRM_URL: _VALID_PRM,
            "https://auth.sonos.com/.well-known/oauth-authorization-server": as_meta,
        })
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch), \
             patch("app.services.mcp_oauth_client.select_endpoints", return_value=endpunkte), \
             patch("app.services.mcp_oauth_client.default_scope", return_value=None):
            result = await mcp_servers.oauth_discover(
                server_id=9, body=None, user=MagicMock(id="u1"), db=_db(server),
            )
        self.assertTrue(result["oauth_enabled"])
        self.assertEqual(result["authorization_endpoint"], "https://auth.sonos.com/authorize")

    async def test_discover_still_aborts_when_nothing_advertises_oauth(self):
        server = _server()
        fetch = _fetch_json_that_only_answers({})
        with patch.object(mcp_servers, "_oauth_probe_challenge", new=AsyncMock(return_value=None)), \
             patch.object(mcp_servers, "_oauth_fetch_json", new=fetch):
            with self.assertRaises(HTTPException) as fall:
                await mcp_servers.oauth_discover(
                    server_id=9, body=None, user=MagicMock(id="u1"), db=_db(server),
                )
        self.assertEqual(fall.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
