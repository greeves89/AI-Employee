"""Welche Agenten gerade ein Sprachgespräch führen — eine Stelle, im Prozess gezählt.

Eine Sprachsitzung schreibt nur an bestimmten Stellen Chat-Zeilen. Wer „ist der Agent
gerade beschäftigt?“ allein an Chat-Zeilen misst (``agenten_aktualisieren.beschaeftigt``),
könnte einen Agenten mitten im Gespräch neu erstellen. Deshalb meldet sich jede Sitzung
hier an, gibt bei jeder Nachricht ein Lebenszeichen und meldet sich im ``finally`` wieder
ab (``api/ws.py``).

Lebenszeichen, weil eine halb offene Verbindung (Tab offen, Gerät im Ruhezustand) sonst
ewig als Gespräch zählte und der Agent nie aktualisiert würde: Ohne Nachricht seit
``FRISCH`` gilt die Sitzung nicht mehr als Gespräch.

Im Prozess reicht: Der Orchestrator läuft mit einem Arbeitsprozess (``--workers 1``), und
der Scheduler, der nachfragt, läuft im selben. Mit mehreren Prozessen wäre diese Stelle
blind — dann gehört sie nach Redis.
"""

import itertools
import time

#: Ohne Nachricht so lange: keine laufende Unterhaltung mehr.
FRISCH = 10 * 60

_offen: dict[int, tuple[str, float]] = {}
_zaehler = itertools.count(1)


def anmelden(agent_id: str) -> int:
    kennung = next(_zaehler)
    _offen[kennung] = (agent_id, time.monotonic())
    return kennung


def lebenszeichen(kennung: int) -> None:
    eintrag = _offen.get(kennung)
    if eintrag is not None:
        _offen[kennung] = (eintrag[0], time.monotonic())


def abmelden(kennung: int) -> None:
    _offen.pop(kennung, None)


def aktiv(agent_id: str) -> bool:
    grenze = time.monotonic() - FRISCH
    return any(aid == agent_id and zuletzt >= grenze for aid, zuletzt in list(_offen.values()))
