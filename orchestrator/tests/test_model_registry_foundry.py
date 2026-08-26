"""Foundry-Discovery im Modell-Katalog (Kundenbefund 2026-08-18).

Fuer "foundry" gab es keinerlei Suchpfad — deployte Claude-Modelle einer
Azure-AI-Foundry-Ressource tauchten nie im Katalog auf, obwohl Ressource und
Key laengst in den Provider-Einstellungen standen.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.services.model_registry_service import _discover_foundry


def _client_returning(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class FoundryDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_resource_or_key_nothing_is_queried(self):
        with patch.object(settings, "foundry_api_key", ""), \
             patch.object(settings, "foundry_resource", ""):
            self.assertEqual(await _discover_foundry(), [])

    async def test_resource_name_builds_the_anthropic_route_and_parses_claude(self):
        payload = {"data": [
            {"id": "claude-opus-5", "display_name": "Claude Opus 5"},
            {"id": "gpt-5.6-sol"},   # kein Claude -> gehoert nicht in claude_code
        ]}
        ctx, client = _client_returning(payload)
        with patch.object(settings, "foundry_api_key", "fk"), \
             patch.object(settings, "foundry_resource", "meine-ressource"), \
             patch("httpx.AsyncClient", return_value=ctx):
            out = await _discover_foundry()
        url = client.get.call_args.args[0]
        self.assertEqual(url, "https://meine-ressource.services.ai.azure.com/anthropic/v1/models")
        headers = client.get.call_args.kwargs["headers"]
        self.assertEqual(headers["x-api-key"], "fk")
        self.assertEqual(headers["api-key"], "fk")
        self.assertEqual(out, [{
            "mode": "claude_code", "provider": "foundry", "value": "claude-opus-5",
            "label": "Claude Opus 5", "tier": "Discovered", "source": "discovered",
        }])

    async def test_full_url_resource_is_used_as_is(self):
        ctx, client = _client_returning({"data": []})
        with patch.object(settings, "foundry_api_key", "fk"), \
             patch.object(settings, "foundry_resource", "https://custom.example.com/"), \
             patch("httpx.AsyncClient", return_value=ctx):
            await _discover_foundry()
        self.assertEqual(client.get.call_args.args[0], "https://custom.example.com/anthropic/v1/models")

    async def test_transport_errors_stay_best_effort(self):
        ctx, client = _client_returning({})
        client.get = AsyncMock(side_effect=RuntimeError("down"))
        with patch.object(settings, "foundry_api_key", "fk"), \
             patch.object(settings, "foundry_resource", "res"), \
             patch("httpx.AsyncClient", return_value=ctx):
            self.assertEqual(await _discover_foundry(), [])


if __name__ == "__main__":
    unittest.main()
