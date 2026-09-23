"""Gesundheit einer verbundenen OAuth-Integration: gilt der Token noch?

Warum es das gibt
-----------------
Die Integrationsseite kannte nur "verbunden" oder "nicht verbunden" — und
"verbunden" hiess lediglich: es gibt eine Zeile in ``oauth_integrations``. Ob der
Token darin noch taugt, stand nirgends. Genau so ist es passiert: der
Anthropic-Refresh-Token lief ab (``invalid_grant: Refresh token expired``), der
Hintergrund-Refresh schrieb alle fuenf Minuten eine Fehlerzeile ins Log, und die
Karte zeigte elf Tage lang gruen "Connected", waehrend jeder Claude-Agent mit 401
antwortete.

Dieses Modul macht daraus drei Zustaende und sorgt dafuer, dass ein endgueltig
gescheiterter Refresh einmal laut gemeldet wird statt nur geloggt:

- ``connected``        Token gueltig, letzte Erneuerung (falls versucht) ging durch.
- ``refresh_failing``  Token noch gueltig, aber die Erneuerung scheitert. Das ist
                        die Vorwarnung: bis ``expires_at`` laeuft noch alles.
- ``expired``          ``expires_at`` liegt in der Vergangenheit. Weil der
                        Hintergrund-Refresh zehn Minuten VOR Ablauf erneuert, heisst
                        das zuverlaessig: die Erneuerung klappt nicht mehr. Nur ein
                        neuer Login hilft.

Den letzten Refresh-Fehler merken wir uns in Redis statt in einer neuen Spalte:
er ist fluechtig (der naechste erfolgreiche Refresh loescht ihn), und es braucht
keine Migration. Fehlt Redis, faellt der Status auf die reine Zeitpruefung zurueck.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTED = "connected"
STATUS_REFRESH_FAILING = "refresh_failing"
STATUS_EXPIRED = "expired"

_FAILURE_KEY = "oauth:refresh_failure:{id}"
_ALERT_KEY = "oauth:refresh_alert:{id}"

#: Wie lange eine Meldung ueber einen endgueltig gescheiterten Refresh vorhaelt,
#: bevor sie erneut rausgeht. Einmal am Tag: laut genug, dass es nicht wieder
#: wochenlang niemand merkt, leise genug, dass es kein Alarmsturm wird (der
#: Hintergrund-Refresh laeuft alle fuenf Minuten).
ALERT_REPEAT_SECONDS = 24 * 3600

#: Der gemerkte Fehler selbst verfaellt nach 30 Tagen. Solange der Refresh
#: scheitert, schreibt ihn der Hintergrundlauf alle fuenf Minuten neu — die TTL
#: raeumt nur Reste weg (Zeile geloescht, ohne dass disconnect() lief).
FAILURE_TTL_SECONDS = 30 * 24 * 3600

#: Fehlercodes nach RFC 6749 §5.2, bei denen ein Retry nichts bringt: der
#: Refresh-Token ist tot, widerrufen oder gehoert nicht (mehr) zu diesem Client.
_PERMANENT_ERRORS = {"invalid_grant", "invalid_client", "unauthorized_client"}


def integration_status(
    connected: bool,
    expires_at: datetime | None,
    refresh_failure: dict | None,
    now: datetime | None = None,
) -> str:
    """Zustand einer Integration aus Ablaufzeit und letztem Refresh-Fehler.

    Ohne ``expires_at`` (z. B. GitHub-PAT) gibt es nichts zu erneuern — dann
    zaehlt allein, ob verbunden ist.
    """
    if not connected:
        return STATUS_DISCONNECTED
    now = now or datetime.now(timezone.utc)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return STATUS_EXPIRED
    if refresh_failure:
        return STATUS_REFRESH_FAILING
    return STATUS_CONNECTED


def describe_refresh_error(status_code: int, body: str) -> tuple[str | None, str]:
    """``(error_code, lesbarer Text)`` aus einer Token-Endpunkt-Antwort.

    Der Rohtext der Antwort geht NIE ungefiltert in UI oder Meldung — nur die
    standardisierten Felder ``error`` / ``error_description``. Tokens stehen in
    einer Fehlerantwort zwar nicht, aber ein unbekannter Anbieter kann Beliebiges
    zurueckgeben.
    """
    code: str | None = None
    description = ""
    try:
        data = json.loads(body or "")
        if isinstance(data, dict):
            raw_code = data.get("error")
            code = raw_code if isinstance(raw_code, str) else None
            raw_desc = data.get("error_description")
            description = raw_desc if isinstance(raw_desc, str) else ""
    except (ValueError, TypeError):
        pass
    text = f"HTTP {status_code}"
    if code:
        text += f" – {code}"
    if description:
        text += f": {description[:200]}"
    return code, text


def is_permanent_failure(status_code: int, error_code: str | None) -> bool:
    """Scheitert der Refresh endgueltig (neuer Login noetig) oder nur voruebergehend?

    5xx und Netzwerkfehler sind voruebergehend — die faengt ``post_refresh_with_retry``
    ohnehin ab und der naechste Lauf versucht es erneut. Ein 400/401 mit einem der
    Codes aus RFC 6749 §5.2 dagegen heilt nicht von selbst.
    """
    if error_code in _PERMANENT_ERRORS:
        return True
    return status_code in (400, 401) and error_code is None


async def get_refresh_failure(redis, integration_id: int | None) -> dict | None:
    """Letzten Refresh-Fehler lesen. ``None`` wenn keiner oder Redis fehlt."""
    client = getattr(redis, "client", None)
    if not client or integration_id is None:
        return None
    try:
        raw = await client.get(_FAILURE_KEY.format(id=integration_id))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 — eine Statusanzeige darf nie die Seite kippen
        logger.debug("Refresh-Fehler fuer Integration %s nicht lesbar", integration_id, exc_info=True)
        return None


async def record_refresh_failure(
    redis, integration_id: int | None, error_text: str, permanent: bool, now: datetime | None = None
) -> bool:
    """Refresh-Fehler festhalten. Gibt zurueck, ob JETZT gemeldet werden soll.

    ``True`` nur bei einem endgueltigen Fehler und nur, wenn die letzte Meldung
    fuer diese Integration laenger als ``ALERT_REPEAT_SECONDS`` her ist (atomar per
    ``SET NX EX`` — zwei parallele Refresh-Laeufe melden nicht doppelt).
    """
    client = getattr(redis, "client", None)
    if not client or integration_id is None:
        return permanent  # ohne Redis lieber melden als schweigen
    now = now or datetime.now(timezone.utc)
    payload = json.dumps({"error": error_text, "permanent": permanent, "at": now.isoformat()})
    try:
        await client.set(_FAILURE_KEY.format(id=integration_id), payload, ex=FAILURE_TTL_SECONDS)
        if not permanent:
            return False
        first = await client.set(
            _ALERT_KEY.format(id=integration_id), now.isoformat(), nx=True, ex=ALERT_REPEAT_SECONDS
        )
        return bool(first)
    except Exception:  # noqa: BLE001
        logger.debug("Refresh-Fehler fuer Integration %s nicht speicherbar", integration_id, exc_info=True)
        return permanent


async def clear_refresh_failure(redis, integration_id: int | None) -> None:
    """Nach erfolgreichem Refresh oder neuem Login: Fehler und Melde-Sperre loeschen.

    Die Melde-Sperre muss mit weg — sonst bliebe ein Rueckfall am selben Tag nach
    einem Neu-Login stumm.
    """
    client = getattr(redis, "client", None)
    if not client or integration_id is None:
        return
    try:
        await client.delete(
            _FAILURE_KEY.format(id=integration_id), _ALERT_KEY.format(id=integration_id)
        )
    except Exception:  # noqa: BLE001
        logger.debug("Refresh-Fehler fuer Integration %s nicht loeschbar", integration_id, exc_info=True)
