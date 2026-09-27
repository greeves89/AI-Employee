"""Wer den Chat eines gestoppten Agenten oeffnet, weckt ihn.

Bis v1.340.1 lehnte der Chat-WebSocket einen gestoppten Agenten mit 4010 ab
und einen Agenten ohne Container (nach einem Update) sogar, ohne es je wieder
zu versuchen. Geweckt wurde nur beim Anmelden — und auch dort nur, wenn noch
ein Container da war. In der einfachen Ansicht fehlt der Neustart-Knopf: der
Nutzer kam an seinen Agenten nicht mehr heran.

Der Test faehrt den echten Handler ueber einen echten WebSocket und ersetzt
nur die Aussenwelt (Datenbank, Docker, Redis, Anmeldung).
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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


async def _zugriff_ok(agent_id, user, db):
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
             patch("app.services.user_lifecycle.wake_agent", wecken):
            with TestClient(app) as client:
                try:
                    with client.websocket_connect(f"/ws/agents/{agent.id}/chat?ticket=t") as sock:
                        erste = sock.receive_json()
                except _Ende:
                    pass
        return erste

    def test_gestoppter_agent_ohne_container_wird_geweckt(self):
        agent = SimpleNamespace(id="a1", container_id=None, mode="claude_code")
        docker = _Docker("exited")
        aufrufe = []

        async def wecken(db, d, agent_id, wait=False, timeout=30):
            aufrufe.append((agent_id, wait))
            agent.container_id = "c-neu"
            docker.status = "running"
            return True

        erste = self._verbinden(agent, docker, wecken)
        self.assertEqual(aufrufe, [("a1", True)])
        self.assertEqual(erste["type"], "ready")

    def test_laufender_agent_wird_nicht_angefasst(self):
        agent = SimpleNamespace(id="a2", container_id="c1", mode="claude_code")
        aufrufe = []

        async def wecken(*a, **k):
            aufrufe.append(a)
            return True

        erste = self._verbinden(agent, _Docker("running"), wecken)
        self.assertEqual(aufrufe, [])
        self.assertEqual(erste["type"], "ready")

    def test_scheitert_das_wecken_bleibt_es_bei_4010(self):
        agent = SimpleNamespace(id="a3", container_id="c1", mode="claude_code")

        async def wecken(*a, **k):
            return False

        with self.assertRaises(WebSocketDisconnect) as fehler:
            self._verbinden(agent, _Docker("exited"), wecken)
        self.assertEqual(fehler.exception.code, 4010)


if __name__ == "__main__":
    unittest.main()
