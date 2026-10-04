"""Bremse gegen Durchprobieren der Anmeldung und Flut im Prüfprotokoll (#908).

Zwei Zähler, beide in Redis (mit Zähler im Prozess als Rückfall — der
Orchestrator läuft mit einem Worker):

- **je Absender-IP**: Fehlversuche bei ``/auth/login`` und ``/auth/mfa/verify``
  gemeinsam. Ab ``IP_MAX_FEHLVERSUCHE`` innerhalb von ``IP_FENSTER_SEKUNDEN`` gibt
  es 429. Erfolgreiche Anmeldungen zählen nicht — viele Menschen hinter einer
  Firmen-IP sollen sich weiter anmelden können. Die IP ist ``request.client.host``,
  genau wie in ``APIRateLimitMiddleware``; ``X-Forwarded-For`` wertet uvicorn
  (``--forwarded-allow-ips``) aus, hier nichts zusätzlich.
- **Fehlanmeldungen mit unbekannter Adresse je Stunde**: Sie landen nicht einzeln
  im Prüfprotokoll (wer mit erfundenen Adressen anklopft, könnte es sonst
  beliebig füllen), sondern gezählt — siehe ``unbekannte_zaehlen``.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

IP_MAX_FEHLVERSUCHE = 20
IP_FENSTER_SEKUNDEN = 10 * 60
_STUNDE = 3600

# Rückfall ohne Redis: {ip: [zeitpunkte]} und {stunde: anzahl}
_ip_lokal: dict[str, list[float]] = {}
_unbekannt_lokal: dict[int, int] = {}


def _zuruecksetzen() -> None:
    """Nur für Tests."""
    _ip_lokal.clear()
    _unbekannt_lokal.clear()


def client_ip(request) -> str:
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def _k_ip(ip: str) -> str:
    return f"anmeldung:ip:{ip}"


def _k_unbekannt(stunde: int) -> str:
    return f"anmeldung:unbekannt:{stunde}"


async def _zaehlen(redis, schluessel: str, ablauf: int) -> int:
    # Ablaufzeit beim Anlegen (SET NX EX), INCR behält sie — kein Dauerzähler (#879).
    await redis.set(schluessel, 0, ex=ablauf, nx=True)
    return int(await redis.incr(schluessel))


async def ip_gesperrt_fuer(redis, ip: str) -> int:
    """Restsekunden der IP-Sperre, 0 wenn frei."""
    if redis is not None:
        try:
            anzahl = int(await redis.get(_k_ip(ip)) or 0)
            if anzahl < IP_MAX_FEHLVERSUCHE:
                return 0
            rest = await redis.ttl(_k_ip(ip))
            return int(rest) if rest and rest > 0 else IP_FENSTER_SEKUNDEN
        except Exception:  # noqa: BLE001 — Redis weg: Rückfall im Prozess
            logger.warning("Anmelde-Drossel: Redis nicht erreichbar, zähle im Prozess")
    jetzt = time.time()
    versuche = [t for t in _ip_lokal.get(ip, []) if jetzt - t < IP_FENSTER_SEKUNDEN]
    _ip_lokal[ip] = versuche
    if len(versuche) < IP_MAX_FEHLVERSUCHE:
        return 0
    return max(1, int(IP_FENSTER_SEKUNDEN - (jetzt - versuche[0])))


async def ip_fehlversuch(redis, ip: str) -> None:
    if redis is not None:
        try:
            anzahl = await _zaehlen(redis, _k_ip(ip), IP_FENSTER_SEKUNDEN)
            if anzahl == IP_MAX_FEHLVERSUCHE:
                logger.warning("Anmeldung: %s Fehlversuche von %s — gesperrt", anzahl, ip)
            return
        except Exception:  # noqa: BLE001
            logger.warning("Anmelde-Drossel: Redis nicht erreichbar, zähle im Prozess")
    _ip_lokal.setdefault(ip, []).append(time.time())


def _meldenswert(anzahl: int) -> bool:
    """Erster Fall der Stunde, dann Zwischenstände bei 10, 100, 1000 …"""
    while anzahl >= 10 and anzahl % 10 == 0:
        anzahl //= 10
    return anzahl == 1


async def unbekannte_zaehlen(redis) -> tuple[int, bool, int]:
    """Eine Fehlanmeldung mit unbekannter Adresse zählen.

    Gibt ``(anzahl_diese_stunde, protokollieren, anzahl_vorige_stunde)`` zurück:
    ``protokollieren`` beim ersten Fall der Stunde und bei 10, 100, 1000 …;
    ``anzahl_vorige_stunde`` nur beim ersten Fall der Stunde (sonst 0) — dann
    gehört die Zusammenfassung der vorigen Stunde ins Protokoll.
    """
    stunde = int(time.time() // _STUNDE)
    anzahl = None
    vorige = 0
    if redis is not None:
        try:
            anzahl = await _zaehlen(redis, _k_unbekannt(stunde), 3 * _STUNDE)
            if anzahl == 1:
                vorige = int(await redis.get(_k_unbekannt(stunde - 1)) or 0)
        except Exception:  # noqa: BLE001
            logger.warning("Anmelde-Drossel: Redis nicht erreichbar, zähle im Prozess")
            anzahl = None
    if anzahl is None:
        anzahl = _unbekannt_lokal.get(stunde, 0) + 1
        _unbekannt_lokal[stunde] = anzahl
        if anzahl == 1:
            vorige = _unbekannt_lokal.get(stunde - 1, 0)
            for alt in [s for s in _unbekannt_lokal if s < stunde - 1]:
                _unbekannt_lokal.pop(alt, None)
    return anzahl, _meldenswert(anzahl), vorige
