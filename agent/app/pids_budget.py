"""Das pids-Limit des Containers als Budget lesen — statt Nebenlaeufigkeit zu raten.

Der pids-Cgroup eines Agent-Containers ist gedeckelt (gemessen: 512). Jeder
gleichzeitige Lauf startet einen vollen Satz MCP-Server und kostet rund 88
Threads. Ab etwa fuenf parallelen Laeufen ist die Grenze erreicht — und dann
scheitert alles, was einen Prozess braucht (``gh``, ``git``, ``pytest``,
``curl``) mit ``EAGAIN``, ohne dass der Lauf davon Notiz nimmt.

Zwei Dinge stehen hier:

1. ``max_concurrent_runs`` leitet die Obergrenze aus ``pids.max`` ab, statt sie
   zu raten. Bei 512 sind das vier Laeufe, nicht fuenf: vier kosten 352, die
   Grundlast 40 — bleiben 120 frei, genau die Reserve fuer die Werkzeuge.
2. ``find_fork_exhaustion`` erkennt die Meldungen, die der Kernel bei
   erschoepftem Budget durchreicht. Ein Lauf, der daran gescheitert ist, darf
   nicht als erledigt gelten.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Reserve fuer Grundlast und Werkzeuge: der Python-Dienst selbst, laufende
# Transkription, Shells — plus Luft fuer das ``gh``/``git``, das der Lauf
# aufrufen will. Ohne diese Reserve ist die Grenze exakt dann erreicht, wenn
# der Lauf sein erstes Werkzeug braucht.
DEFAULT_RESERVE = 120

# Kosten eines voll hochgefahrenen Laufs, gemessen: 11 MCP-Server a 7-11
# Threads plus der ``claude``-Prozess mit 8.
DEFAULT_COST_PER_RUN = 88

# Ist das Budget nicht lesbar (kein Linux, cgroup v1 ohne die Datei, keine
# Rechte), gilt serielle Ausfuehrung. Lieber langsam als erdrosselt.
FALLBACK_MAX_CONCURRENT = 1

_CGROUP_V2 = "/sys/fs/cgroup"
_CGROUP_V1 = "/sys/fs/cgroup/pids"

#: Was der Kernel meldet, wenn kein Prozess und kein Thread mehr frei ist.
#: Jede dieser Zeilen bedeutet: das Werkzeug ist nicht gelaufen.
FORK_EXHAUSTION_MARKERS = (
    "resource temporarily unavailable",
    "cannot fork()",
    "fork: retry",
    "failed to create new os thread",
    "newosproc",
    "blockingioerror",
    "[errno 11]",
)


def _read_int(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    if raw == "max":  # kein Limit gesetzt
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def read_pids_limits() -> tuple[int | None, int | None]:
    """``(pids_current, pids_max)`` des eigenen Containers, cgroup v2 vor v1.

    Beide Werte einzeln optional: ``pids.current`` kann lesbar sein, obwohl
    ``pids.max`` auf ``max`` steht.
    """
    for base in (_CGROUP_V2, _CGROUP_V1):
        current = _read_int(os.path.join(base, "pids.current"))
        limit = _read_int(os.path.join(base, "pids.max"))
        if current is not None or limit is not None:
            return current, limit
    return None, None


def max_concurrent_runs(
    pids_max: int | None = None,
    reserve: int = DEFAULT_RESERVE,
    cost_per_run: int = DEFAULT_COST_PER_RUN,
) -> int:
    """Wie viele Laeufe gleichzeitig ins pids-Budget passen — mindestens einer.

    ``(pids_max - reserve) / cost_per_run``. Ist ``pids_max`` unbekannt, gilt
    ``FALLBACK_MAX_CONCURRENT``; abstuerzen waere hier das Schlechteste, weil
    der Agent dann gar nicht mehr arbeitet.
    """
    if pids_max is None:
        _, pids_max = read_pids_limits()
    if pids_max is None:
        logger.info(
            "pids.max nicht lesbar — Nebenlaeufigkeit bleibt bei %d",
            FALLBACK_MAX_CONCURRENT,
        )
        return FALLBACK_MAX_CONCURRENT
    if cost_per_run <= 0:
        return FALLBACK_MAX_CONCURRENT
    return max(1, (pids_max - reserve) // cost_per_run)


def find_fork_exhaustion(text: str | None) -> str | None:
    """Die erste Zeile, die nach erschoepftem pids-Budget aussieht — sonst ``None``.

    Absichtlich die ZEILE und nicht nur ``True``: wer den Task spaeter ansieht,
    soll lesen koennen, welches Werkzeug es erwischt hat.
    """
    if not text:
        return None
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in FORK_EXHAUSTION_MARKERS):
            return line.strip()[:200]
    return None


def exhaustion_message(evidence: str) -> str:
    """Der Grund, der am Task stehen soll — mit dem gemessenen Stand."""
    current, limit = read_pids_limits()
    measured = (
        f"pids {current}/{limit}"
        if current is not None and limit is not None
        else "pids-Stand nicht lesbar"
    )
    return (
        "Abgebrochen: dem Container sind die Prozesse ausgegangen "
        f"({measured}). Werkzeuge konnten nicht starten, das Ergebnis waere "
        f"unvollstaendig. Belegzeile: {evidence}"
    )
