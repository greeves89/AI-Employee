"""Ein Orchestrator-Neustart reisst die laufende Antwort im Chat nicht mehr ab.

Bis hierher hielt der Chat-WebSocket die Zuordnung „Nachricht -> Gespraech"
nur im Speicher der Verbindung (``_mid_to_session``). Ein Neustart des
Orchestrators trennt jede Verbindung; die neue kannte die laufende Nachricht
nicht, verwarf jedes weitere Ereignis des Agenten als „fremd", und die Antwort
blieb als Bruchstueck stehen oder verschwand — obwohl der Agent-Container
weiterarbeitete.

Jetzt steht die Zuordnung in Redis (mit Ablaufzeit), und der Browser sagt nach
dem Wiederverbinden, welches Gespraech er zeigt (``action: attach``). Die Tests
fahren den echten Handler ueber einen echten WebSocket — einmal vor, einmal
nach dem „Neustart" — und ersetzen nur die Aussenwelt. Die Redis-Attrappe ist
so streng wie Redis: Bytes zurueck, Ablaufzeit Pflicht, Pub/Sub nur an die, die
im Moment des Sendens abonniert haben.
"""
import ast
import asyncio
import json
import textwrap
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api import ws


class _RedisFehler(Exception):
    """Was redis-py bei einem ungueltigen Aufruf wirft."""


class _PubSub:
    def __init__(self, server):
        self.server = server
        self.kanaele: set[str] = set()
        self.eingang: deque = deque()
        self.tot = False

    async def subscribe(self, kanal):
        self.kanaele.add(kanal)

    async def unsubscribe(self, kanal=None):
        self.kanaele.discard(kanal)

    async def aclose(self):
        self.kanaele.clear()

    async def get_message(self, ignore_subscribe_messages=False, timeout=0.0):
        if self.tot:
            # Der Prozess ist weg — nach einem Neustart hoert dieser Abonnent
            # nichts mehr, und der Nachlauf der alten Verbindung findet nie statt.
            raise ConnectionError("Verbindung zu Redis verloren")
        if self.eingang:
            kanal, daten = self.eingang.popleft()
            return {"type": "message", "channel": kanal.encode(), "data": daten}
        await asyncio.sleep(min(timeout or 0.0, 0.02))
        return None


class _StrengesRedis:
    """Genug Redis fuer den Chat-Handler — und nicht nachsichtiger als Redis."""

    def __init__(self):
        self._werte: dict[str, bytes] = {}
        self._ablauf: dict[str, float] = {}
        self._listen: dict[str, list[bytes]] = {}
        self._abonnenten: list[_PubSub] = []
        self._sperre = threading.Lock()
        self.jetzt = time.monotonic()

    # -- Hilfen -------------------------------------------------------------
    @staticmethod
    def _bytes(wert) -> bytes:
        if isinstance(wert, bytes):
            return wert
        if isinstance(wert, (str, int, float)) and not isinstance(wert, bool):
            return str(wert).encode()
        raise _RedisFehler(f"Ungueltiger Wert fuer Redis: {type(wert).__name__}")

    def _lebt(self, schluessel: str) -> bool:
        frist = self._ablauf.get(schluessel)
        if frist is not None and frist <= self.jetzt:
            self._werte.pop(schluessel, None)
            self._ablauf.pop(schluessel, None)
            return False
        return schluessel in self._werte

    # -- Schluessel -----------------------------------------------------------
    async def setex(self, schluessel, sekunden, wert):
        if not isinstance(sekunden, int) or sekunden <= 0:
            raise _RedisFehler("invalid expire time in 'setex' command")
        self._werte[schluessel] = self._bytes(wert)
        self._ablauf[schluessel] = self.jetzt + sekunden

    async def get(self, schluessel):
        return self._werte[schluessel] if self._lebt(schluessel) else None

    def ttl(self, schluessel) -> int:
        if not self._lebt(schluessel):
            return -2
        frist = self._ablauf.get(schluessel)
        return -1 if frist is None else int(frist - self.jetzt)

    async def hgetall(self, schluessel):
        return {}

    # -- Listen ---------------------------------------------------------------
    async def lpush(self, schluessel, *werte):
        liste = self._listen.setdefault(schluessel, [])
        for w in werte:
            liste.insert(0, self._bytes(w))
        return len(liste)

    async def rpush(self, schluessel, *werte):
        liste = self._listen.setdefault(schluessel, [])
        liste.extend(self._bytes(w) for w in werte)
        return len(liste)

    async def ltrim(self, schluessel, start, ende):
        return True

    async def llen(self, schluessel):
        return len(self._listen.get(schluessel, []))

    def liste(self, schluessel) -> list[bytes]:
        return list(self._listen.get(schluessel, []))

    # -- Pub/Sub --------------------------------------------------------------
    def pubsub(self):
        ps = _PubSub(self)
        with self._sperre:
            self._abonnenten.append(ps)
        return ps

    async def publish(self, kanal, nachricht):
        return self.einspeisen(kanal, nachricht)

    def einspeisen(self, kanal, nachricht) -> int:
        daten = self._bytes(nachricht)
        empfaenger = 0
        with self._sperre:
            for ps in self._abonnenten:
                if not ps.tot and kanal in ps.kanaele:
                    ps.eingang.append((kanal, daten))
                    empfaenger += 1
        return empfaenger

    def abonnenten(self, kanal) -> int:
        with self._sperre:
            return sum(1 for ps in self._abonnenten if not ps.tot and kanal in ps.kanaele)

    def neustart_des_orchestrators(self):
        """Redis ueberlebt, die Abonnenten des alten Prozesses nicht."""
        with self._sperre:
            for ps in self._abonnenten:
                ps.tot = True


class _RedisDienst:
    def __init__(self, client):
        self.client = client

    async def subscribe(self, kanal):
        ps = self.client.pubsub()
        await ps.subscribe(kanal)
        return ps


class _Db:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, key):
        return SimpleNamespace(id=key)


async def _angemeldet(websocket, token=None, ticket=None):
    websocket.state.user_id = "u1"
    return True


async def _zugriff_ok(agent_id, user, db):
    return None


def _warte_bis(bedingung, frist=3.0):
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if bedingung():
            return
        time.sleep(0.01)
    raise AssertionError("Bedingung nicht rechtzeitig erfuellt")


class NeustartTests(unittest.TestCase):
    AGENT = "a1"
    KANAL = "agent:a1:chat:response"

    def setUp(self):
        self.redis = _StrengesRedis()
        app = FastAPI()
        app.include_router(ws.router)
        self._patches = [
            patch.object(ws, "_redis", _RedisDienst(self.redis)),
            patch.object(ws, "_docker", None),
            patch.object(ws, "_authenticate_ws", _angemeldet),
            patch.object(ws, "async_session_factory", lambda: _Db()),
            patch("app.dependencies.require_agent_access", _zugriff_ok),
            # Die Datenbank weiss hier nichts — die Zuordnung MUSS aus Redis kommen.
            patch("app.services.chat_persistence.session_for_message",
                  AsyncMock(return_value=None)),
        ]
        for p in self._patches:
            p.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for p in reversed(self._patches):
            p.stop()

    def _verbinden(self):
        sock = self.client.websocket_connect(f"/ws/agents/{self.AGENT}/chat?ticket=t&client_id=tab1")
        sock.__enter__()
        self.assertEqual(sock.receive_json()["type"], "ready")
        return sock

    def _ereignis(self, mid, art="text", **daten):
        return json.dumps({"agent_id": self.AGENT, "message_id": mid, "type": art,
                           "data": daten, "timestamp": "2026-10-03T10:00:00+00:00"})

    def _nachricht_senden(self, sitzung="s1") -> str:
        vorher = len(self.redis.liste(f"agent:{self.AGENT}:chat"))
        sock = self._verbinden()
        sock.send_json({"text": "Wie ist der Stand?", "session_id": sitzung})
        _warte_bis(lambda: len(self.redis.liste(f"agent:{self.AGENT}:chat")) > vorher)
        auftrag = json.loads(self.redis.liste(f"agent:{self.AGENT}:chat")[0])
        return sock, auftrag["id"]

    def test_zuordnung_ueberlebt_neustart_und_antwort_laeuft_weiter(self):
        alt, mid = self._nachricht_senden("s1")
        # Die Zuordnung steht in Redis — mit Ablaufzeit, nicht fuer immer.
        schluessel = f"chat:msg:{mid}:session"
        self.assertEqual(self.redis._werte.get(schluessel), b"s1")
        self.assertGreater(self.redis.ttl(schluessel), 0)
        self.assertLessEqual(self.redis.ttl(schluessel), 24 * 3600)

        # Neustart: der alte Prozess ist weg, Redis bleibt.
        self.redis.neustart_des_orchestrators()
        alt.__exit__(None, None, None)

        neu = self._verbinden()
        neu.send_json({"action": "attach", "session_id": "s1"})
        bestaetigt = neu.receive_json()
        self.assertEqual((bestaetigt["type"], bestaetigt["session_id"]), ("attached", "s1"))
        _warte_bis(lambda: self.redis.abonnenten(self.KANAL) == 1)

        # Der Agent arbeitet weiter und sendet den naechsten Textblock.
        self.redis.einspeisen(self.KANAL, self._ereignis(mid, text="weiter im Text"))
        ankunft = neu.receive_json()
        self.assertEqual(ankunft["message_id"], mid)
        self.assertEqual(ankunft["session_id"], "s1")
        self.assertEqual(ankunft["data"]["text"], "weiter im Text")
        neu.__exit__(None, None, None)

    def test_fremdes_gespraech_bleibt_nach_neustart_draussen(self):
        alt, mid_fremd = self._nachricht_senden("s2")
        alt.__exit__(None, None, None)
        alt, mid_eigen = self._nachricht_senden("s1")
        self.redis.neustart_des_orchestrators()
        alt.__exit__(None, None, None)

        neu = self._verbinden()
        neu.send_json({"action": "attach", "session_id": "s1"})
        self.assertEqual(neu.receive_json()["type"], "attached")
        _warte_bis(lambda: self.redis.abonnenten(self.KANAL) == 1)

        # Erst das fremde, dann das eigene Ereignis: Die Reihenfolge bleibt, also
        # beweist das zuerst ankommende eigene, dass das fremde verworfen wurde.
        self.redis.einspeisen(self.KANAL, self._ereignis(mid_fremd, text="geheim"))
        self.redis.einspeisen(self.KANAL, self._ereignis(mid_eigen, text="meins"))
        ankunft = neu.receive_json()
        self.assertEqual((ankunft["message_id"], ankunft["data"]["text"]), (mid_eigen, "meins"))
        neu.__exit__(None, None, None)

    def test_ohne_attach_bleibt_es_bei_der_abschottung(self):
        """Eine neue Verbindung, die nicht sagt, was sie zeigt, bekommt nichts Fremdes."""
        alt, mid = self._nachricht_senden("s1")
        self.redis.neustart_des_orchestrators()
        alt.__exit__(None, None, None)

        neu = self._verbinden()
        _warte_bis(lambda: self.redis.abonnenten(self.KANAL) == 1)
        self.redis.einspeisen(self.KANAL, self._ereignis(mid, text="laufend"))
        # Kontrollereignis ohne message_id kommt immer durch — und zeigt, dass
        # das vorherige nicht weitergereicht wurde.
        self.redis.einspeisen(self.KANAL, json.dumps({"type": "kontrolle", "data": {}}))
        self.assertEqual(neu.receive_json()["type"], "kontrolle")
        neu.__exit__(None, None, None)


class ZuordnungOhneRedisEintragTests(unittest.IsolatedAsyncioTestCase):
    """Nachrichten von VOR diesem Stand haben keinen Redis-Eintrag. Fuer sie
    weiss die Datenbank (Nutzerzeile) das Gespraech — einmal gefragt, nicht je
    Ereignis."""

    async def test_rueckfall_auf_die_nutzerzeile_einmal_je_nachricht(self):
        redis = _StrengesRedis()
        db = AsyncMock(return_value="s1")
        eigene, gefunden, gefragt = {}, {}, set()
        with patch("app.services.chat_persistence.session_for_message", db):
            for _ in range(3):
                sid = await ws._sitzung_fuer_ereignis(
                    redis, "a1", "m-alt", "s1", eigene, gefunden, gefragt)
                self.assertEqual(sid, "s1")
        db.assert_awaited_once_with("a1", "m-alt")

    async def test_unbekannt_fragt_die_datenbank_nur_einmal(self):
        redis = _StrengesRedis()
        db = AsyncMock(return_value=None)
        eigene, gefunden, gefragt = {}, {}, set()
        with patch("app.services.chat_persistence.session_for_message", db):
            for _ in range(5):
                self.assertIsNone(await ws._sitzung_fuer_ereignis(
                    redis, "a1", "m-x", "s1", eigene, gefunden, gefragt))
        self.assertEqual(db.await_count, 1)


def _process_event_mit_zustand():
    """Die echte, in ``ws_agent_chat`` eingebettete ``_process_event`` — per AST
    ausgeschnitten wie in test_context_tokens_persist, hier mit Zugriff auf
    die Menge der von DIESER Verbindung gesendeten Nachrichten."""
    quelle_ws = (Path(ws.__file__)).read_text()
    knoten = next(
        n for n in ast.walk(ast.parse(quelle_ws))
        if isinstance(n, ast.FunctionDef) and n.name == "_process_event"
    )
    zeilen = quelle_ws.splitlines(keepends=True)
    quelle = textwrap.dedent("".join(zeilen[knoten.lineno - 1:knoten.end_lineno]))
    huelle = (
        "def _huelle(json, _auto_presented_files_from_text,\n"
        "            _auto_presented_files_from_tool_calls, _pending_message_ids):\n"
        "    _streaming_responses, _seen_tool_ids = {}, set()\n"
        + textwrap.indent(quelle, "    ")
        + "    return _process_event\n"
    )
    ns: dict = {}
    exec(huelle, ns)
    eigene: set = set()
    return ns["_huelle"](json, lambda c: [], lambda t: [], eigene), eigene


class SchlussstandTests(unittest.TestCase):
    """Wer einen Zug erst mittendrin sieht (Wiederverbindung), hat nur den Rest
    des Textes. Gespeichert wird dann der vollstaendige Text aus dem ``done`` —
    sonst ueberschriebe der Rest die fertige Antwort in der Datenbank."""

    def _ereignisse(self, verarbeite, mid):
        verarbeite(json.dumps({"type": "text", "message_id": mid, "data": {"text": "…Rest"}}))
        return verarbeite(json.dumps({"type": "done", "message_id": mid, "data": {
            "text": "Anfang …Rest",
            "tool_calls": [{"tool": "Read", "input": "{}"}],
        }}))

    def test_mittendrin_uebernommen_speichert_den_vollen_text(self):
        verarbeite, _eigene = _process_event_mit_zustand()
        art, _mid, resp, _meta = self._ereignisse(verarbeite, "m1")
        self.assertEqual(art, "done")
        self.assertEqual(resp["content"], "Anfang …Rest")
        self.assertEqual(resp["tool_calls"], [{"tool": "Read", "input": "{}"}])

    def test_eigener_strom_behaelt_das_mitgeschriebene(self):
        verarbeite, eigene = _process_event_mit_zustand()
        eigene.add("m1")
        _art, _mid, resp, _meta = self._ereignisse(verarbeite, "m1")
        self.assertEqual(resp["content"], "…Rest")


if __name__ == "__main__":
    unittest.main()
