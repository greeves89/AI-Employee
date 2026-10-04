"""Bremse gegen Durchprobieren der Anmeldung und Flut im Prüfprotokoll (#908).

Zwei Zähler, beide in Redis (mit Zähler im Prozess als Rückfall — der
Orchestrator läuft mit einem Worker):

- **je Absender-IP**: Fehlversuche bei ``/auth/login`` und ``/auth/mfa/verify``
  gemeinsam, gleitend über ``IP_FENSTER_MINUTEN`` (Minuten-Eimer). Ab
  ``IP_MAX_FEHLVERSUCHE`` gibt es 429 — nur für DIESE Adresse, nie für ein Konto
  an sich. Erfolgreiche Anmeldungen zählen nicht (viele Menschen hinter einer
  Firmen-IP), die Grenze ist großzügig, die Sperre kurz: wer aufhört, ist nach
  wenigen Minuten wieder frei. Ohne eindeutige Adresse (``core/client_ip``) gibt
  es KEINE IP-Sperre — hinter einem Proxy sähen sonst alle gleich aus.
- **Fehlanmeldungen mit unbekannter Adresse je Stunde und Weg**: nicht einzeln im
  Prüfprotokoll (wer mit erfundenen Adressen anklopft, könnte es sonst beliebig
  füllen), sondern gezählt — siehe ``unbekannte_zaehlen``. Ein Eintrag gilt erst
  als geschrieben, wenn ``gemeldet_merken`` nach dem Festschreiben lief; schlägt
  das Schreiben fehl, meldet der nächste Fall erneut — keine stille Lücke.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

IP_MAX_FEHLVERSUCHE = 30
IP_FENSTER_MINUTEN = 10
_STUNDE = 3600
_AUFBEWAHREN = 3 * _STUNDE

# Rückfall ohne Redis
_ip_lokal: dict[str, list[float]] = {}
_unbekannt_lokal: dict[str, int] = {}
_marken_lokal: set[str] = set()


def _zuruecksetzen() -> None:
    """Nur für Tests."""
    _ip_lokal.clear()
    _unbekannt_lokal.clear()
    _marken_lokal.clear()


def _warnen() -> None:
    logger.warning("Anmelde-Drossel: Redis nicht erreichbar, zähle im Prozess")


# --- je Absender-IP ----------------------------------------------------------


def _k_ip(ip: str, minute: int) -> str:
    return f"anmeldung:ip:{ip}:{minute}"


async def ip_gesperrt_fuer(redis, ip: str | None) -> int:
    """Restsekunden der IP-Sperre, 0 wenn frei (oder ohne eindeutige Adresse)."""
    if not ip:
        return 0
    jetzt = time.time()
    minute = int(jetzt // 60)
    if redis is not None:
        try:
            summe = 0
            for m in range(minute - IP_FENSTER_MINUTEN + 1, minute + 1):
                summe += int(await redis.get(_k_ip(ip, m)) or 0)
            # Gleitend: spaetestens zur naechsten vollen Minute faellt der aelteste Eimer weg.
            return max(1, int(60 - jetzt % 60)) if summe >= IP_MAX_FEHLVERSUCHE else 0
        except Exception:  # noqa: BLE001 — Redis weg: Rückfall im Prozess
            _warnen()
    fenster = IP_FENSTER_MINUTEN * 60
    versuche = [t for t in _ip_lokal.get(ip, []) if jetzt - t < fenster]
    _ip_lokal[ip] = versuche
    if len(versuche) < IP_MAX_FEHLVERSUCHE:
        return 0
    return max(1, int(fenster - (jetzt - versuche[-IP_MAX_FEHLVERSUCHE])))


async def ip_fehlversuch(redis, ip: str | None) -> None:
    if not ip:
        return
    if redis is not None:
        try:
            schluessel = _k_ip(ip, int(time.time() // 60))
            # Ablaufzeit beim Anlegen (SET NX EX), INCR behält sie — kein Dauerzähler (#879).
            await redis.set(schluessel, 0, ex=(IP_FENSTER_MINUTEN + 1) * 60, nx=True)
            await redis.incr(schluessel)
            return
        except Exception:  # noqa: BLE001
            _warnen()
    _ip_lokal.setdefault(ip, []).append(time.time())


# --- unbekannte Adressen -----------------------------------------------------


@dataclass
class Zaehlung:
    stunde: int
    anzahl: int          # Fälle dieser Stunde (auf diesem Weg), diesen eingeschlossen
    melden: bool         # ein Eintrag für diese Stunde ist (noch) fällig
    vorige: int          # Fälle der vorigen Stunde, deren Zusammenfassung noch fehlt (0 = keine)


def _meilenstein(anzahl: int) -> bool:
    """Zwischenstände bei 10, 100, 1000 …"""
    if anzahl < 10:
        return False
    while anzahl % 10 == 0:
        anzahl //= 10
    return anzahl == 1


def _k_zahl(weg: str, stunde: int) -> str:
    return f"anmeldung:unbekannt:{weg}:{stunde}"


def _k_gemeldet(weg: str, stunde: int) -> str:
    return f"anmeldung:unbekannt:{weg}:{stunde}:gemeldet"


def _k_zusammengefasst(weg: str, stunde: int) -> str:
    return f"anmeldung:unbekannt:{weg}:{stunde}:zusammengefasst"


async def unbekannte_zaehlen(redis, weg: str) -> Zaehlung:
    """Eine Fehlanmeldung mit unbekannter Adresse zählen und sagen, was ins Protokoll gehört."""
    stunde = int(time.time() // _STUNDE)
    if redis is not None:
        try:
            await redis.set(_k_zahl(weg, stunde), 0, ex=_AUFBEWAHREN, nx=True)
            anzahl = int(await redis.incr(_k_zahl(weg, stunde)))
            gemeldet = bool(await redis.get(_k_gemeldet(weg, stunde)))
            vorige = 0
            if not await redis.get(_k_zusammengefasst(weg, stunde - 1)):
                vorige = int(await redis.get(_k_zahl(weg, stunde - 1)) or 0)
            return Zaehlung(stunde, anzahl, not gemeldet or _meilenstein(anzahl),
                            vorige if vorige > 1 else 0)
        except Exception:  # noqa: BLE001
            _warnen()
    anzahl = _unbekannt_lokal.get(_k_zahl(weg, stunde), 0) + 1
    _unbekannt_lokal[_k_zahl(weg, stunde)] = anzahl
    gemeldet = _k_gemeldet(weg, stunde) in _marken_lokal
    vorige = 0
    if _k_zusammengefasst(weg, stunde - 1) not in _marken_lokal:
        vorige = _unbekannt_lokal.get(_k_zahl(weg, stunde - 1), 0)
    return Zaehlung(stunde, anzahl, not gemeldet or _meilenstein(anzahl), vorige if vorige > 1 else 0)


async def _marke_setzen(redis, schluessel: str) -> None:
    if redis is not None:
        try:
            await redis.set(schluessel, 1, ex=_AUFBEWAHREN)
            return
        except Exception:  # noqa: BLE001
            _warnen()
    _marken_lokal.add(schluessel)


async def gemeldet_merken(redis, weg: str, stunde: int) -> None:
    """Erst NACH dem Festschreiben aufrufen."""
    await _marke_setzen(redis, _k_gemeldet(weg, stunde))


async def zusammenfassung_merken(redis, weg: str, stunde: int) -> None:
    """Erst NACH dem Festschreiben aufrufen (``stunde`` = die zusammengefasste)."""
    await _marke_setzen(redis, _k_zusammengefasst(weg, stunde))
