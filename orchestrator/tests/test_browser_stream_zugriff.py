"""Wer darf die Browser-Arbeitsflaeche eines Agenten sehen? (#828)

Der Bildstrom zeigt die Seiten, an denen der Agent gerade arbeitet — und weil
sich der Nutzer dort selbst anmeldet, zeigt er **angemeldete Sitzungen**.
Damit ist dieser Kanal mindestens so schutzbeduerftig wie das Gespraech
selbst; die Plattform ist userbased, ein fremder Agent geht niemanden etwas an.

Geprueft wird deshalb die Reihenfolge der Tore, nicht die Bildqualitaet:

1. **Nicht angemeldet** -> raus, und zwar bevor irgendetwas ueber den Agenten
   verraten wird.
2. **Angemeldet, aber kein Zugriff auf DIESEN Agenten** -> raus (IDOR).
3. **Zugriff, aber kein laufender Container** -> saubere Absage statt
   Verbindungsversuch ins Leere.

Der dritte Punkt ist kein Schoenheitsfehler: Ohne ihn liefe der Proxy in einen
Namensaufloesungs-Fehler, und der Nutzer saehe "Verbindung weg" ohne Grund.
"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import ws as ws_modul


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(ws_modul.router)
    return app


class _EchterNutzer:
    id = "u1"


async def _abgewiesen(websocket, token=None, ticket=None):
    """Wie das echte ``_authenticate_ws`` bei einer Abweisung: es SCHLIESST.

    Eine Attrappe, die nur ``False`` zurueckgibt, laesst den Testclient ewig
    auf den Handschlag warten — und bildet damit auch nicht ab, was wirklich
    passiert.
    """
    await websocket.close(code=4001, reason="Authentication failed")
    return False


class BrowserStromZugriff(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(_app())

    #: Der Router traegt das Praefix "/ws" — ohne das trifft der Test ins
    #: Leere und meldet trotzdem Erfolg, weil eine fehlende Route ebenfalls
    #: eine Trennung ausloest. Genau so war diese Datei zuerst falsch gruen.
    PFAD = "/ws/agents/a1/browser"

    def _verbinden(self):
        return self.client.websocket_connect(self.PFAD)

    def test_die_route_existiert_ueberhaupt(self):
        """Wache gegen falsch gruene Zugriffstests.

        Alle Pruefungen unten erwarten eine Trennung. Eine Route, die es gar
        nicht gibt, trennt auch — dann pruefen sie nichts mehr.

        FastAPI >=0.141 loest include_router() lazy auf: app.routes enthaelt
        dann nur noch _IncludedRouter-Wrapper ohne .path (siehe WiringTests
        in test_concierge.py fuer denselben Kniff). iter_route_contexts()
        legt die Routen wieder flach — aber fuer WebSocket-Routen bleibt
        rc.path dabei leer, der echte Pfad steckt in rc.route.path.
        """
        try:
            from fastapi.routing import iter_route_contexts

            pfade = [
                rc.route.path
                for rc in iter_route_contexts(_app().routes)
                if getattr(rc.route, "path", "").endswith("/browser")
            ]
        except ImportError:
            pfade = [r.path for r in _app().routes if getattr(r, "path", "").endswith("/browser")]
        self.assertIn(self.PFAD.replace("a1", "{agent_id}"), pfade, f"Vorhandene Routen: {pfade}")

    # --- Tor 1: Anmeldung --------------------------------------------------

    def test_ohne_anmeldung_kein_strom(self):
        with patch.object(ws_modul, "_authenticate_ws", new=_abgewiesen):
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with self._verbinden() as ws:
                    ws.receive_json()
        self.assertEqual(ctx.exception.code, 4001)

    def test_die_anmeldung_kommt_vor_allem_anderen(self):
        """Ohne Anmeldung darf nicht einmal nachgesehen werden, ob es den
        Agenten gibt — sonst waere der Kanal ein Verzeichnis fremder Agenten."""
        nachgesehen = AsyncMock(return_value="ai-agent-test-a1")
        with patch.object(ws_modul, "_authenticate_ws", new=_abgewiesen), \
             patch.object(ws_modul, "_agent_container_name", new=nachgesehen):
            with self.assertRaises(WebSocketDisconnect):
                with self._verbinden() as ws:
                    ws.receive_json()
        nachgesehen.assert_not_awaited()

    # --- Tor 2: Eigentuemer ------------------------------------------------

    def test_fremder_agent_bleibt_verschlossen(self):
        """Der eigentliche IDOR."""
        async def _angemeldet(websocket, token=None, ticket=None):
            await websocket.accept()
            websocket.state.user_id = "u1"
            return True

        verweigern = AsyncMock(side_effect=HTTPException(status_code=403))
        gesucht = AsyncMock(return_value="ai-agent-test-a1")
        with patch.object(ws_modul, "_authenticate_ws", new=_angemeldet), \
             patch("app.dependencies.require_agent_access", new=verweigern), \
             patch("app.db.session.async_session_factory"), \
             patch.object(ws_modul, "_agent_container_name", new=gesucht):
            with self.assertRaises(WebSocketDisconnect):
                with self._verbinden() as ws:
                    ws.receive_json()
        gesucht.assert_not_awaited()

    # --- Tor 3: Container --------------------------------------------------

    def test_ohne_container_saubere_absage(self):
        async def _angemeldet(websocket, token=None, ticket=None):
            await websocket.accept()
            websocket.state.user_id = "u1"
            return True

        class _Db:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return _EchterNutzer()

        with patch.object(ws_modul, "_authenticate_ws", new=_angemeldet), \
             patch("app.dependencies.require_agent_access", new=AsyncMock(return_value=None)), \
             patch.object(ws_modul, "async_session_factory", new=lambda: _Db()), \
             patch.object(ws_modul, "_agent_container_name", new=AsyncMock(return_value=None)):
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with self._verbinden() as ws:
                    ws.receive_json()
        self.assertEqual(ctx.exception.code, 4004,
                         "Erwartet war die eigene Absage 'kein Container', kein "
                         "allgemeiner Fehler — sonst sucht der Nutzer an der "
                         "falschen Stelle.")


class FehlermeldungErreichtDenNutzer(unittest.TestCase):
    """Ist der Agent nicht erreichbar, muss die Meldung ankommen — nicht ein 500.

    ``_authenticate_ws`` nimmt die Verbindung NICHT an; das tut jeder Kanal
    selbst. Wurde das vergessen, endete jeder Fehlerpfad in "ASGI callable
    returned without completing handshake": Der Nutzer sah einen nackten
    Serverfehler statt des Hinweises, der hier eigens formuliert ist.
    """

    def test_unerreichbarer_agent_meldet_sich_verstaendlich(self):
        async def _angemeldet(websocket, token=None, ticket=None):
            websocket.state.user_id = "u1"
            return True

        class _Db:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return _EchterNutzer()

        with patch.object(ws_modul, "_authenticate_ws", new=_angemeldet), \
             patch("app.dependencies.require_agent_access", new=AsyncMock(return_value=None)), \
             patch.object(ws_modul, "async_session_factory", new=lambda: _Db()), \
             patch("app.core.agent_wakeup.ensure_agent_running", new=AsyncMock(return_value=True)), \
             patch.object(ws_modul, "_agent_container_name",
                          new=AsyncMock(return_value="ai-agent-gibtsnicht-a1")):
            with TestClient(_app()).websocket_connect(BrowserStromZugriff.PFAD) as ws:
                nachricht = ws.receive_json()

        self.assertEqual(nachricht.get("typ"), "fehler", nachricht)
        self.assertIn("Browser", nachricht["text"])


class SchlafenderAgent(unittest.TestCase):
    """Ein schlafender Agent wird geweckt, statt den Nutzer abzuweisen.

    Agenten steigen nach ihrer Ruhezeit aus. Wer den Browser-Reiter oeffnet,
    tut das aber gerade, WEIL er etwas sehen will — eine Absage "kein
    Container" waere die unbrauchbarste aller Antworten. Nachrichten und
    Besprechungen wecken laengst ueber ``ensure_agent_running``; dieser Kanal
    benutzt denselben Weg, statt einen zweiten zu bauen.
    """

    def test_es_wird_geweckt_bevor_aufgegeben_wird(self):
        async def _angemeldet(websocket, token=None, ticket=None):
            await websocket.accept()
            websocket.state.user_id = "u1"
            return True

        class _Db:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return _EchterNutzer()

        reihenfolge = []

        async def _wecken(agent_id, docker, redis):
            reihenfolge.append("wecken")
            return True

        async def _suchen(agent_id):
            reihenfolge.append("suchen")
            return None

        with patch.object(ws_modul, "_authenticate_ws", new=_angemeldet), \
             patch("app.dependencies.require_agent_access", new=AsyncMock(return_value=None)), \
             patch.object(ws_modul, "async_session_factory", new=lambda: _Db()), \
             patch("app.core.agent_wakeup.ensure_agent_running", new=_wecken), \
             patch.object(ws_modul, "_agent_container_name", new=_suchen):
            with self.assertRaises(WebSocketDisconnect):
                with TestClient(_app()).websocket_connect(BrowserStromZugriff.PFAD) as ws:
                    ws.receive_json()

        self.assertEqual(
            reihenfolge, ["wecken", "suchen"],
            "Erst wecken, dann nach dem Container sehen — andersherum waere der "
            "Weckversuch wirkungslos.",
        )


if __name__ == "__main__":
    unittest.main()
