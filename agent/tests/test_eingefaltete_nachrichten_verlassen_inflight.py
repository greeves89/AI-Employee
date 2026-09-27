"""Eingefaltete Chatnachrichten muessen die Inflight-Liste wieder verlassen.

Auf einer Anlage kam von einem Agenten dieselbe Antwort 17-mal: "Weiterhin derselbe
alte Verlauf, keine neuen Inhalte." In ``agent:<id>:chat:inflight`` lagen 17
alte Nachrichten des Nutzers — seit Tagen.

Der Weg dahin, im parallelen Modus:

1. Die Hauptschleife holt jede Nachricht aus der Queue, legt sie zur
   Absturzsicherung in die Inflight-Liste und reicht sie an die Spur ihrer
   Sitzung weiter.
2. Laeuft in der Spur gerade ein Zug, faltet ``_drain_pending`` die wartenden
   Nachrichten in diesen Zug ein (Live-Steering).
3. Entfernt wurde danach nur die Nachricht, die den Zug gestartet hatte. Die
   eingefalteten blieben in der Inflight-Liste.
4. Beim naechsten Start legt ``_requeue_inflight`` sie alle zurueck — sie werden
   wieder eingefaltet, bleiben wieder liegen. Bei einem Agenten, der jede
   Stunde fuer einen Zeitplan geweckt wird, heisst das: jede Stunde derselbe
   alte Block.

Geprueft wird Verhalten, nicht Quelltext: der echte Consumer, eine Redis-
Attrappe mit echten Listen-Operationen.
"""

import asyncio
import json

from app.chat_consumer import ChatConsumer


class _Redis:
    """Genug von redis.asyncio fuer Queue und Inflight-Liste."""

    def __init__(self):
        self.listen: dict[str, list[bytes]] = {}

    def _l(self, k):
        return self.listen.setdefault(k, [])

    async def lpush(self, k, v):
        self._l(k).insert(0, v if isinstance(v, bytes) else v.encode()); return len(self._l(k))

    async def rpush(self, k, v):
        self._l(k).append(v if isinstance(v, bytes) else v.encode()); return len(self._l(k))

    async def lpop(self, k):
        return self._l(k).pop(0) if self._l(k) else None

    async def rpop(self, k):
        return self._l(k).pop() if self._l(k) else None

    async def lrem(self, k, anzahl, v):
        v = v if isinstance(v, bytes) else v.encode()
        weg = 0
        while v in self._l(k) and (anzahl == 0 or weg < anzahl):
            self._l(k).remove(v); weg += 1
        return weg

    async def llen(self, k):
        return len(self._l(k))


def _nachricht(i: int) -> bytes:
    return json.dumps({"id": f"m{i}", "text": f"Nachricht {i}", "source": "webapp",
                       "chat_session_id": "sitzung-1"}).encode()


async def _eine_runde_mit_einfalten(anzahl_eingefaltet: int) -> _Redis:
    """Wie im parallelen Modus: alle Nachrichten liegen in inflight und in der
    Spur; die erste startet den Zug, die uebrigen werden darin eingefaltet."""
    c = ChatConsumer("a1")
    r = _Redis()
    c.redis = r
    c._sem = asyncio.Semaphore(2)
    sk = c._source_key("webapp", "sitzung-1", None)
    c._prepare_text = lambda text, *a, **k: text          # nur der Text interessiert hier

    spur: asyncio.Queue = asyncio.Queue()
    c._lanes[sk] = spur
    roh = [_nachricht(i) for i in range(1 + anzahl_eingefaltet)]
    for m in roh:
        await r.lpush(c.inflight_key, m)                  # so macht es die Hauptschleife
        await spur.put((json.loads(m), m))

    eingefaltet: list[str] = []

    async def zug(msg, log_publisher):
        eingefaltet.extend(await c._drain_pending(sk))    # Live-Steering waehrend des Zugs
        c.running = False                                 # danach endet die Spur

    c._process_one = zug
    c.running = True
    await asyncio.wait_for(c._lane_worker(sk, spur, None), timeout=5)
    assert eingefaltet == [f"Nachricht {i}" for i in range(1, 1 + anzahl_eingefaltet)], \
        "Vorbedingung: die Nachrichten wurden tatsaechlich eingefaltet"
    return r


def test_nach_dem_zug_ist_inflight_leer():
    r = asyncio.run(_eine_runde_mit_einfalten(2))
    assert r.listen.get("agent:a1:chat:inflight") == [], (
        "Eingefaltete Nachrichten blieben in inflight — sie kaemen bei jedem Start erneut."
    )


def test_ohne_einfalten_bleibt_es_wie_bisher():
    r = asyncio.run(_eine_runde_mit_einfalten(0))
    assert r.listen.get("agent:a1:chat:inflight") == []


def test_ein_neustart_stellt_nichts_erneut_zu():
    """Der eigentliche Schaden: nach einem Neustart kam der alte Block wieder."""
    r = asyncio.run(_eine_runde_mit_einfalten(3))
    c = ChatConsumer("a1")
    c.redis = r
    asyncio.run(c._requeue_inflight())
    assert r.listen.get("agent:a1:chat", []) == [], "Nach dem Neustart wurde Altes erneut zugestellt."
