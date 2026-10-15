"""Freigaben erholen sich: ein Aussetzer versteckt news_search nicht fuer immer.

Re-Review zu #812, K3: Der Python-Pfad (Custom-LLM, Sprachfront) speicherte
einen Fehlschlag von ``/agent-search/capabilities`` fuer die ganze Lebenszeit
des Agentenprozesses, und beide Handler hielten ihren Katalog zusaetzlich fest.
Eine spaetere Freigabe oder das Ende eines Orchestrator-Aussetzers kam nie an.
Der MCP-Pfad (Claude Code, Codex) fragt dagegen bei jedem ``tools/list`` neu.

Geprueft mit beiden echten ``_get_catalog()``-Methoden: Ausfall → Erholung,
Verweigerung → spaetere Freigabe, und dass ein Erfolg nicht bei jedem Zug
eine neue Anfrage ausloest.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.tools import capabilities


class _Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Antworten:
    """Nacheinander vorbereitete Antworten von /agent-search/capabilities."""

    def __init__(self, *antworten):
        self._antworten = list(antworten)
        self.aufrufe = 0

    async def __call__(self, methode, pfad, *a, **k):
        self.aufrufe += 1
        antwort = self._antworten.pop(0)
        if isinstance(antwort, Exception):
            raise antwort
        return antwort


def _client(antworten):
    client = AsyncMock()
    client._request = antworten
    client.close = AsyncMock()
    return client


class _Basis(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        capabilities._zwischenspeicher = None
        capabilities._gueltig_bis = 0.0
        self.uhr = _Uhr()
        p = patch.object(capabilities, "_jetzt", self.uhr)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(setattr, capabilities, "_zwischenspeicher", None)

    def antworten(self, *antworten):
        self.a = _Antworten(*antworten)
        p = patch("app.tools.api_client.OrchestratorAPIClient", lambda: _client(self.a))
        p.start()
        self.addCleanup(p.stop)


class FreigabenTests(_Basis):
    async def test_a_failure_is_retried_after_a_short_time(self):
        self.antworten(ConnectionError("orchestrator weg"), {"web": True, "news": True})
        self.assertEqual(await capabilities.freigaben(), {})
        self.uhr.t += capabilities.GUELTIG_FEHLER + 1
        self.assertEqual(await capabilities.freigaben(), {"web": True, "news": True})

    async def test_a_failure_is_not_retried_on_every_turn(self):
        self.antworten(ConnectionError("weg"), {"news": True})
        await capabilities.freigaben()
        self.uhr.t += 1
        await capabilities.freigaben()
        self.assertEqual(self.a.aufrufe, 1)

    async def test_a_success_is_kept_for_a_while_then_refreshed(self):
        self.antworten({"news": False}, {"news": True})
        self.assertEqual(await capabilities.freigaben(), {"news": False})
        self.uhr.t += capabilities.GUELTIG_ERFOLG - 1
        self.assertEqual(await capabilities.freigaben(), {"news": False})
        self.assertEqual(self.a.aufrufe, 1)
        self.uhr.t += 2
        self.assertEqual(await capabilities.freigaben(), {"news": True})


def _namen(katalog):
    return {(t.get("function") or {}).get("name") or t.get("name") for t in katalog}


class _KatalogBasis(_Basis):
    def handler(self):
        raise NotImplementedError

    async def test_outage_then_recovery_shows_the_tool_again(self):
        self.antworten(ConnectionError("weg"), {"web": True, "news": True})
        h = self.handler()
        self.assertNotIn("news_search", _namen(await h._get_catalog()))
        self.uhr.t += capabilities.GUELTIG_FEHLER + 1
        self.assertIn("news_search", _namen(await h._get_catalog()))

    async def test_a_later_grant_reaches_a_running_handler(self):
        self.antworten({"news": False}, {"news": True})
        h = self.handler()
        self.assertNotIn("news_search", _namen(await h._get_catalog()))
        self.uhr.t += capabilities.GUELTIG_ERFOLG + 1
        self.assertIn("news_search", _namen(await h._get_catalog()))

    async def test_a_revoked_grant_removes_the_tool(self):
        self.antworten({"news": True}, {"news": False})
        h = self.handler()
        self.assertIn("news_search", _namen(await h._get_catalog()))
        self.uhr.t += capabilities.GUELTIG_ERFOLG + 1
        self.assertNotIn("news_search", _namen(await h._get_catalog()))

    async def test_mcp_tools_are_discovered_once_and_kept(self):
        self.antworten({"news": False}, {"news": True})
        h = self.handler()
        await h._get_catalog()
        self.uhr.t += capabilities.GUELTIG_ERFOLG + 1
        katalog = await h._get_catalog()
        self.assertIn("mcp_beispiel", _namen(katalog))
        h._mcp_client.discover_tools.assert_awaited_once()


def _mcp():
    client = AsyncMock()
    client.discover_tools = AsyncMock(return_value=[
        {"type": "function", "function": {"name": "mcp_beispiel", "description": "x", "parameters": {}}},
    ])
    return client


class ChatHandlerKatalogTests(_KatalogBasis):
    def handler(self):
        from app.llm_chat_handler import LLMChatHandler
        h = LLMChatHandler.__new__(LLMChatHandler)
        h._all_tools = None
        h._activated = []
        h._mcp_client = _mcp()
        return h


class RunnerKatalogTests(_KatalogBasis):
    def handler(self):
        from app.llm_runner import LLMRunner
        h = LLMRunner.__new__(LLMRunner)
        h._all_tools = None
        h._activated = []
        h._mcp_client = _mcp()
        return h


del _KatalogBasis  # nur die beiden echten Handler laufen


if __name__ == "__main__":
    unittest.main()
