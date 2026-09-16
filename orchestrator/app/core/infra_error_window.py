"""Kurzzeitgedaechtnis fuer Infrastruktur-Fehler im Protokoll (#746 Punkt 3).

Ein Alarm wie „Sentinel verstummt" sagt nur, WAS ausgefallen ist — nicht,
WARUM. Am 15.09.2026 kam er mitten in einem 13-minuetigen DNS-Aussetzer: Redis
war nicht erreichbar, also konnte der Sentinel sein Lebenszeichen nicht
schreiben. Der Leser bekam eine ``urgent``-Nachricht ueber einen
Ueberwachungsausfall, waehrend das Protokoll daneben voller
``Temporary failure in name resolution`` stand.

Dieser Handler haengt am Wurzel-Logger und merkt sich von jedem WARNING+-
Eintrag nur, OB er eine der bekannten Infrastruktur-Signaturen traegt und
WANN er kam. Wer einen Alarm formuliert, fragt ``zusammenfassung()`` und kann
dazuschreiben, ob im selben Zeitraum DNS-/Redis-/DB-Fehler liefen.

Die Signaturen werden gegen Nachricht UND Ausnahmetext geprueft: der
gaierror des 15.09. stand nicht in der Nachricht (``Future exception was
never retrieved``), sondern nur im angehaengten ``exc_info``.

Der Puffer ist begrenzt (``maxlen``), der Handler tut sonst nichts — ein
Protokoll-Handler, der selbst Last erzeugt oder wirft, waere absurd.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import timedelta

# Reihenfolge = Prioritaet: die erste Signatur, die passt, bestimmt die Klasse.
# DNS steht vor Redis, weil ein Redis-Verbindungsfehler WEGEN Namensaufloesung
# beide Woerter tragen kann — und dann ist DNS die Ursache.
_SIGNATUREN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dns", (
        "name resolution",
        "gaierror",
        "Name or service not known",
        "nodename nor servname",
    )),
    # Bewusst KEIN nacktes „Redis": eine Zeile, die Redis nur erwaehnt (etwa
    # der Alarm-Hinweis selbst: „KEINE Redis-/DNS-/DB-Fehler"), ist kein
    # Verbindungsfehler. Nur die echten Fehlertexte des Clients zaehlen.
    ("redis", (
        "Timeout connecting to server",
        "Connection closed by server",
        "connecting to redis",
        "redis.exceptions",
        "RedisACL error",
    )),
    ("db", (
        "resilient_session",
        "asyncpg",
        "OperationalError",
        "connection to server at",
    )),
)

_KLASSEN: tuple[str, ...] = tuple(k for k, _ in _SIGNATUREN)

_BESCHRIFTUNG = {
    "dns": "DNS-Aussetzer (Namensaufloesung)",
    "redis": "Redis-Verbindungsfehler",
    "db": "Datenbank-Verbindungsfehler",
}


# logging.LogRecord-Attribut (per ``extra={IGNORIEREN_MARKER: True}``), mit dem
# sich eine Zeile vom Zaehlen ausnimmt.
IGNORIEREN_MARKER = "infra_fenster_ignorieren"


def _ausnahmekette(exc: BaseException, tiefe: int = 5) -> str:
    """Typ + Text der Ausnahme und ihrer Ursachen (``__cause__``/``__context__``).

    ``raise RuntimeError(...) from gaierror`` traegt den DNS-Fehler eine Ebene
    tiefer; wer nur oben liest, sieht eine Sperre statt einer Namensaufloesung.
    """
    teile: list[str] = []
    gesehen: set[int] = set()
    aktuell: BaseException | None = exc
    while aktuell is not None and len(teile) < tiefe and id(aktuell) not in gesehen:
        gesehen.add(id(aktuell))
        teile.append(f"{type(aktuell).__name__}: {aktuell}")
        aktuell = aktuell.__cause__ or aktuell.__context__
    return "\n".join(teile)


def klassifiziere(text: str) -> str | None:
    """Erste passende Infrastruktur-Klasse fuer einen Protokolltext, sonst None."""
    for klasse, muster in _SIGNATUREN:
        if any(m in text for m in muster):
            return klasse
    return None


class InfraErrorWindow(logging.Handler):
    """Merkt sich Zeitpunkt + Klasse der juengsten Infrastruktur-Fehler."""

    def __init__(self, maxlen: int = 2000, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self._eintraege: deque[tuple[float, str]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 — logging-API
        try:
            # Wer das Fenster ZITIERT (der Alarm selbst), darf nicht hineinzaehlen.
            if getattr(record, IGNORIEREN_MARKER, False):
                return
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text = f"{text}\n{_ausnahmekette(record.exc_info[1])}"
            elif record.exc_text:
                text = f"{text}\n{record.exc_text}"
            klasse = klassifiziere(text)
            if klasse is None:
                return
            with self._lock:
                self._eintraege.append((record.created, klasse))
        except Exception:  # noqa: BLE001 — ein Handler darf das Protokoll nie stoeren
            return

    def zusammenfassung(
        self,
        fenster: timedelta = timedelta(minutes=10),
        jetzt: float | None = None,
    ) -> dict[str, int]:
        """Anzahl je Klasse innerhalb der letzten ``fenster``; jede Klasse ist enthalten."""
        grenze = (jetzt if jetzt is not None else time.time()) - fenster.total_seconds()
        zaehler = {k: 0 for k in _KLASSEN}
        with self._lock:
            eintraege = list(self._eintraege)
        for created, klasse in eintraege:
            if created >= grenze:
                zaehler[klasse] += 1
        return zaehler

    def leeren(self) -> None:
        with self._lock:
            self._eintraege.clear()


_fenster: InfraErrorWindow | None = None


def setup_infra_error_window() -> InfraErrorWindow:
    """Handler einmalig am Wurzel-Logger anbringen (idempotent) und zurueckgeben."""
    global _fenster
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, InfraErrorWindow):
            _fenster = h
            return h
    handler = InfraErrorWindow()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)
    _fenster = handler
    return handler


def get_infra_error_window() -> InfraErrorWindow | None:
    """Der installierte Handler — oder None, wenn nie eingerichtet (Tests, lokal)."""
    return _fenster


def ursachen_hinweis(
    fenster: timedelta = timedelta(minutes=10),
    jetzt: float | None = None,
) -> str:
    """Ein Satz fuer Alarm-Nachrichten: welche Infrastruktur-Fehler liefen gleichzeitig.

    Liefert auch dann einen Satz, wenn nichts protokolliert wurde — dann ist
    der Alarm vermutlich ein echter Ausfall des Dienstes, und genau das soll
    der Leser erfahren. Ohne installierten Handler wird ehrlich gesagt, dass
    nichts gemessen wurde, statt „keine Fehler" zu behaupten.
    """
    minuten = int(fenster.total_seconds() // 60)
    if _fenster is None:
        return (
            f"Ob in den letzten {minuten} Minuten Redis-/DNS-Fehler protokolliert "
            "wurden, ist nicht gemessen (Fehlerfenster nicht eingerichtet)."
        )
    zaehler = _fenster.zusammenfassung(fenster, jetzt)
    teile = [f"{n}x {_BESCHRIFTUNG[k]}" for k, n in zaehler.items() if n]
    if not teile:
        return (
            f"In den letzten {minuten} Minuten wurden KEINE Redis-/DNS-/DB-Fehler "
            "protokolliert — der Ausfall liegt vermutlich im Dienst selbst."
        )
    return (
        f"Im selben Zeitraum (letzte {minuten} Minuten) protokolliert: "
        + ", ".join(teile)
        + ". Vermutlich ein Infrastruktur-Aussetzer, kein Absturz des Dienstes — "
        "Entwarnung folgt, sobald das Lebenszeichen wieder frisch ist."
    )
