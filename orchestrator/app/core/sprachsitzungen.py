"""Welche Agenten gerade ein Sprachgespräch führen — eine Stelle, im Prozess gezählt.

Eine Sprachsitzung schreibt nur an bestimmten Stellen Chat-Zeilen. Wer „ist der Agent
gerade beschäftigt?“ allein an Chat-Zeilen misst (``agenten_aktualisieren.beschaeftigt``),
könnte einen Agenten mitten im Gespräch neu erstellen. Deshalb meldet sich jede Sitzung
hier an und im ``finally`` wieder ab (``api/ws.py``).

Im Prozess reicht: Der Orchestrator läuft mit einem Arbeitsprozess, und der Scheduler,
der nachfragt, läuft im selben.
"""

from collections import Counter

_offen: Counter[str] = Counter()


def anmelden(agent_id: str) -> None:
    _offen[agent_id] += 1


def abmelden(agent_id: str) -> None:
    if _offen[agent_id] <= 1:
        _offen.pop(agent_id, None)
    else:
        _offen[agent_id] -= 1


def aktiv(agent_id: str) -> bool:
    return _offen.get(agent_id, 0) > 0
