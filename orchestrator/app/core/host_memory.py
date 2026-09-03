"""Ob der Host ueberhaupt Speicherlimits durchsetzen kann.

Befund #653: Auf einem Host mit ``cgroup_disable=memory`` in der Kernel-Zeile
fehlt der Speicher-Controller. Daraus folgt dreierlei, und alles davon still:

1. Docker kann kein Speicherlimit je Container durchsetzen — ``mem_limit`` wird
   ohne Fehlermeldung ignoriert.
2. Es gibt keine Buchfuehrung je Container. ``memory.events`` (der OOM-Zaehler)
   existiert nicht, ein Abschuss hinterlaesst also nirgends eine Spur, die einem
   Container zuzuordnen waere.
3. Bei Knappheit greift der globale OOM-Killer des Kernels und sucht sich das
   groesste Opfer — typischerweise einen laufenden Agenten. Der Orchestrator
   sieht davon nur ``Connection closed by server``.

Gemessen wurde: ein einzelner Lauf belegt rund 1,04 GB, davon 691 MB allein die
MCP-Server. Vier gleichzeitige Laeufe sind 4,2 GB auf einem Host mit 7,95 GB —
bei zu 95 % belegtem Auslagerungsspeicher. Zehn von neunzehn fehlgeschlagenen
Aufgaben in einer Woche gingen darauf zurueck, und die Suche lief jedes Mal ins
Leere, weil nichts protokolliert war.

Dieses Modul stellt nur die Sichtbarkeit her. Der eigentliche Hebel ist #638
(eingebaute MCP-Server container-weit statt je Lauf); die Host-Aenderung selbst
(``cgroup_enable=memory``) braucht einen Neustart und ist die Entscheidung des
Betreibers.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CONTROLLER = Path("/sys/fs/cgroup/cgroup.controllers")
_CMDLINE = Path("/proc/cmdline")


def speicher_controller_da() -> bool | None:
    """Ist der cgroup-Speicher-Controller verfuegbar?

    ``None``, wenn es sich nicht feststellen laesst (kein Linux, kein
    cgroup2-Dateisystem) — dann gibt es nichts zu melden, statt etwas zu
    behaupten.
    """
    try:
        return "memory" in _CONTROLLER.read_text().split()
    except OSError:
        return None


def abgeschaltet_per_kernelzeile() -> bool:
    """Wurde er ausdruecklich abgeschaltet? Das ist der behebbare Fall."""
    try:
        return "cgroup_disable=memory" in _CMDLINE.read_text()
    except OSError:
        return False


def hinweis() -> str | None:
    """Der Satz fuer das Protokoll — oder ``None``, wenn alles in Ordnung ist."""
    if speicher_controller_da() is not False:
        return None
    text = (
        "cgroup-Speicher-Controller nicht verfuegbar: Container-Speicherlimits "
        "sind hier wirkungslos, und ein Abschuss durch den Kernel hinterlaesst "
        "keine zuordenbare Spur. Er erscheint dann als 'Connection closed by "
        "server' (#653)."
    )
    if abgeschaltet_per_kernelzeile():
        text += (
            " Ursache ist 'cgroup_disable=memory' in der Kernel-Zeile; sie laesst "
            "sich entfernen — das erfordert einen Neustart des Hosts."
        )
    return text


def beim_start_melden() -> None:
    """Einmal beim Hochfahren sagen, dass Limits hier nichts bewirken.

    Bewusst eine Warnung und kein Fehler: Die Anlage laeuft, sie laeuft nur ohne
    dieses Netz. Ein Abbruch ohne jede Spur ist das eigentliche Aergernis.
    """
    text = hinweis()
    if text:
        logger.warning("[Host] %s", text)
