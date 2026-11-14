"""Wer einem gestoppten Agenten im Chat schreibt, weckt ihn — Oeffnen allein nicht.

Bis v1.340.1 lehnte der Chat-WebSocket einen gestoppten Agenten mit 4010 ab
und einen Agenten ohne Container (nach einem Update) sogar, ohne es je wieder
zu versuchen. Geweckt wurde nur beim Anmelden — und auch dort nur, wenn noch
ein Container da war. In der einfachen Ansicht fehlt der Neustart-Knopf: der
Nutzer kam an seinen Agenten nicht mehr heran. Ab 1.340.2 weckte schon das
Oeffnen; seit 14.11.2026 (Entscheidung Daniel) erst die erste Nachricht: reines
Nachsehen kostet keine Rechenzeit.

Der Test faehrt den echten Handler ueber einen echten WebSocket und ersetzt
nur die Aussenwelt (Datenbank, Docker, Redis, Anmeldung).
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import ws


class _Docker:
    def __init__(self, status):
        self.status = status

    def get_container_status(self, container_id):
        return self.status


class _Db:
    def __init__(self, agent):
        self.agent = agent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, key):
        return SimpleNamespace(id=key)

    async def execute(self, stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self.agent)

    async def refresh(self, obj):
        return None


class _Ende(Exception):
    """Nach "ready" abonniert der Handler Redis — dort endet der Test."""


class _Redis:
    client = object()

    async def subscribe(self, channel):
        # Erst enden, wenn der Test "ready" gelesen hat — sonst ueberholt der
        # Fehler die schon gesendete Nachricht.
        await asyncio.sleep(0.3)
        raise _Ende


async def _angemeldet(websocket, token=None, ticket=None):
    websocket.state.user_id = "u1"
    return True


async def _zugriff_ok(agent_id, user, db, **_):
    return None


class ChatWecktTests(unittest.TestCase):
    def _verbinden(self, agent, docker, wecken):
        app = FastAPI()
        app.include_router(ws.router)
        erste = None
        with patch.object(ws, "_redis", _Redis()), \
             patch.object(ws, "_docker", docker), \
             patch.object(ws, "_authenticate_ws", _angemeldet), \
             patch.object(ws, "async_session_factory", lambda: _Db(agent)), \
             patch("app.dependencies.require_agent_access", _zugriff_ok), \
             patch("app.core.ownership.nur_nutzbare_agent_ids", AsyncMock(return_value=set())), \
             patch("app.services.user_lifecycle.wake_agent", wecken):
            with TestClient(app) as client:
                try:
                    with client.websocket_connect(f"/ws/agents/{agent.id}/chat?ticket=t") as sock:
                        erste = sock.receive_json()
                except _Ende:
                    pass
        return erste

    def test_oeffnen_weckt_nicht_und_meldet_schlaf(self):
        agent = SimpleNamespace(id="a1", container_id=None, mode="claude_code")
        aufrufe = []

        async def wecken(*a, **k):
            aufrufe.append(a)
            return True

        erste = self._verbinden(agent, _Docker("exited"), wecken)
        self.assertEqual(aufrufe, [], "Oeffnen darf den Agenten nicht starten")
        self.assertEqual(erste["type"], "ready")
        self.assertIs(erste["data"]["schlaeft"], True)

    def test_laufender_agent_wird_nicht_angefasst(self):
        agent = SimpleNamespace(id="a2", container_id="c1", mode="claude_code")
        aufrufe = []

        async def wecken(*a, **k):
            aufrufe.append(a)
            return True

        erste = self._verbinden(agent, _Docker("running"), wecken)
        self.assertEqual(aufrufe, [])
        self.assertEqual(erste["type"], "ready")
        self.assertIs(erste["data"]["schlaeft"], False)

    def test_gestoppter_agent_oeffnet_statt_4010(self):
        """Auch wenn Wecken scheitern wuerde: der Verlauf ist lesbar."""
        agent = SimpleNamespace(id="a3", container_id="c1", mode="claude_code")

        async def wecken(*a, **k):
            return False

        erste = self._verbinden(agent, _Docker("exited"), wecken)
        self.assertEqual(erste["type"], "ready")


class WeckenBeimSenden(unittest.TestCase):
    """Die erste Nachricht weckt — vor dem Einreihen, mit derselben Halt-Regel."""

    def setUp(self):
        import inspect
        self.quelle = inspect.getsource(ws.ws_agent_chat)

    def test_vor_dem_einreihen(self):
        wecken = self.quelle.index("if await _agent_wecken():")
        einreihen = self.quelle.index("einreihung = await _chat_auftrag.einreihen(")
        self.assertLess(wecken, einreihen)
        self.assertIn("if _schlaeft and _docker:", self.quelle)

    def test_nur_wer_verwaltet_hebt_den_halt_auf(self):
        block = self.quelle.split("async def _agent_wecken")[1].split("return bereit")[0]
        self.assertIn("durch_mensch=not _nur_nutzen", block)
        self.assertIn("_chat_wake_lock(agent_id)", block)

    def test_scheitert_es_erfaehrt_es_der_mensch(self):
        self.assertIn('"type": "agent_schlaeft"', self.quelle)

    def test_oeffnen_weckt_nicht_mehr(self):
        vor_ready = self.quelle.split('"type": "ready"')[0]
        self.assertNotIn("wake_agent(", vor_ready)


if __name__ == "__main__":
    unittest.main()
