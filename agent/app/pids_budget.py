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

# Dasselbe, wenn die eingebauten Server GEMEINSAM laufen (#638, Phase 3): dann
# gehoeren sie zur Grundlast des Containers, nicht mehr zu jedem einzelnen Lauf.
# Uebrig bleibt der ``claude``-Prozess. Gemessen wurde der Sammelprozess mit
# 7 Threads und 82 MB — gegenueber 81 Threads und ~691 MB fuer dieselben Server
# einzeln.
COST_PER_RUN_GEMEINSAM = 8

# Was der Sammelprozess einmalig kostet; er laeuft, solange der Container laeuft.
RESERVE_GEMEINSAMER_MCP = 10


def _env_override(name: str) -> int | None:
    """Ein AUSDRUECKLICH gesetzter Wert aus der Umgebung — sonst ``None``.

    Das ``None`` ist der eigentliche Zweck. Es unterscheidet "der Betreiber hat
    eine Zahl vorgegeben" von "hier steht nichts, rechne selbst". Genau diese
    Unterscheidung fehlte und hat die Auto-Erkennung unwirksam gemacht (#326):
    die Aufrufer reichten einen VORGABEWERT durch, nie ``None``, und damit war
    der Zweig, der den gemeinsamen MCP-Modus erkennt, in der Produktion tot —
    gerechnet wurde immer mit 88 statt mit 8, also mit 4 Laeufen statt 47.
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r ist keine Zahl — wird ignoriert", name, raw)
        return None


def gemeinsame_mcp_routen() -> tuple[int, set[str]]:
    """Port UND die Routen, die der Sammelprozess GERADE bedient.

    Drei Stufen, und jede war noetig:

    ``MCP_HTTP_PORT`` sagt nur, dass beim Start einmal ein Sammelprozess
    gemeint war. Ein offener Port sagt nur, dass irgendetwas lauscht. Beides
    genuegt nicht, denn ``_all.mjs`` laedt jeden Server EINZELN und laeuft
    bewusst weiter, wenn einer ausfaellt — lieber ein Werkzeug weniger als gar
    keins.

    ``/health`` nennt die tatsaechlich geladenen Namen. Danach wird gefragt.
    Bei jedem Zweifel kommt eine leere Menge zurueck, und der Aufrufer bleibt
    beim bisherigen Weg.
    """
    import json
    import urllib.request

    try:
        port = int(os.environ.get("MCP_HTTP_PORT") or 0)
    except (TypeError, ValueError):
        return 0, set()
    if port <= 0:
        return 0, set()

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=2
        ) as antwort:
            daten = json.loads(antwort.read().decode("utf-8", "replace"))
        routen = {str(n) for n in daten.get("servers") or []}
    except Exception as e:
        logger.warning(
            "MCP_HTTP_PORT=%d gesetzt, aber /health antwortet nicht (%s) — "
            "es gilt der Einzelprozess-Modus", port, e,
        )
        return 0, set()

    if not routen:
        return 0, set()
    return port, routen


def _gemeinsamer_mcp_modus() -> bool:
    """Laufen die eingebauten Server GERADE in EINEM Prozess?

    Gefragt wird die Anlage, nicht die Umgebungsvariable. ``MCP_HTTP_PORT`` ist
    eine Absicht von frueher: ``_start_combined_mcp`` (``agent/app/main.py``)
    faellt bei einem Fehlschlag auf Einzelprozesse zurueck, LAESST die Variable
    aber gesetzt. Wer ihr glaubt, rechnet dann mit 8 Threads je Lauf, waehrend
    real 88 anfallen — 47 zugelassene Laeufe gegen ein 512er-Budget. Ab da
    scheitert jedes ``gh``/``git`` mit ``EAGAIN``, und der Lauf meldet trotzdem
    Erfolg.

    Wird hier zu billig gerechnet, erstickt der Container am pids-Limit —
    deshalb ist bei jedem Zweifel die teure Annahme die richtige.
    """
    return bool(gemeinsame_mcp_routen()[1])

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
    reserve: int | None = None,
    cost_per_run: int | None = None,
) -> int:
    """Wie viele Laeufe gleichzeitig ins pids-Budget passen — mindestens einer.

    OHNE Argumente aufrufen. Die Vorgaben aus ``PIDS_RESERVE`` und
    ``PIDS_COST_PER_RUN`` liest die Funktion selbst; wer sie stattdessen als
    Argument durchreicht, liefert nie ``None`` und schaltet damit die
    Auto-Erkennung unten ab. Die Argumente sind fuer Tests da, die einen
    bestimmten Fall festnageln wollen.

    ``(pids_max - reserve) / cost_per_run``. Ist ``pids_max`` unbekannt, gilt
    ``FALLBACK_MAX_CONCURRENT``; abstuerzen waere hier das Schlechteste, weil
    der Agent dann gar nicht mehr arbeitet.

    Laufen die eingebauten MCP-Server gemeinsam (#638), kostet ein Lauf nur noch
    den ``claude``-Prozess statt zusaetzlich elf Serverprozesse. Bei 512 Plaetzen
    sind das rund 47 gleichzeitige Laeufe statt vier — der Sprung, um den es bei
    dem Umbau ging.
    """
    if reserve is None:
        reserve = _env_override("PIDS_RESERVE")
    if cost_per_run is None:
        cost_per_run = _env_override("PIDS_COST_PER_RUN")
    if cost_per_run is None or reserve is None:
        gemeinsam = _gemeinsamer_mcp_modus()
        if cost_per_run is None:
            cost_per_run = COST_PER_RUN_GEMEINSAM if gemeinsam else DEFAULT_COST_PER_RUN
        if reserve is None:
            reserve = DEFAULT_RESERVE + (RESERVE_GEMEINSAMER_MCP if gemeinsam else 0)
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
