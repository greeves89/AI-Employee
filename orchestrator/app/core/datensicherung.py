"""Stand der Datensicherung (#892) — was ``scripts/backup.sh`` zuletzt gemeldet hat.

Das Sicherungsskript läuft auf dem Host (Cron/systemd), nicht im Orchestrator.
Damit die Oberfläche trotzdem sagen kann, wann zuletzt gesichert wurde, meldet
das Skript am Ende einen Herzschlag an ``POST /admin/backup-status`` — mit einem
lokalen Schlüssel aus der ``.env`` (``BACKUP_STATUS_TOKEN``).

Gespeichert wird in ``platform_settings`` unter einem Schlüssel; eine eigene
Tabelle wäre für einen einzigen Datensatz zu viel.

Die Ampel kommt aus ``attention.backup_state`` — dieselbe Regel für die Karte
unter Admin → Betrieb und für den Concierge, damit beide nicht auseinanderlaufen.
"""

import hmac
import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import attention
from app.models.platform_settings import PlatformSettings

SCHLUESSEL = "backup_status"


def token_passt(gesendet: str | None, erwartet: str | None) -> bool:
    """Zeitkonstanter Vergleich; ohne eingerichteten Schlüssel passt nichts."""
    if not erwartet or not gesendet:
        return False
    return hmac.compare_digest(gesendet.encode("utf-8"), erwartet.encode("utf-8"))


def _zeit(wert: str | None) -> datetime | None:
    if not wert:
        return None
    try:
        zeit = datetime.fromisoformat(wert)
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


async def lese(db: AsyncSession) -> dict:
    zeile = await db.get(PlatformSettings, SCHLUESSEL)
    if zeile is None or not zeile.value:
        return {}
    try:
        stand = json.loads(zeile.value)
    except ValueError:
        return {}
    return stand if isinstance(stand, dict) else {}


async def melde(
    db: AsyncSession,
    *,
    status: str,
    groesse_bytes: int | None = None,
    dauer_s: int | None = None,
    volumes: int | None = None,
    schritt: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Eine Meldung übernehmen. Ein Erfolg hebt einen früheren Fehler auf.

    Committet nicht — das macht der Aufrufer.
    """
    now = now or datetime.now(timezone.utc)
    stand = await lese(db)
    if status == "ok":
        stand.update({
            "zuletzt_ok": now.isoformat(),
            "groesse_bytes": groesse_bytes,
            "dauer_s": dauer_s,
            "volumes": volumes,
            "letzter_fehler": None,
        })
    else:
        stand["letzter_fehler"] = {"zeit": now.isoformat(), "schritt": (schritt or "")[:200]}

    zeile = await db.get(PlatformSettings, SCHLUESSEL)
    if zeile is None:
        db.add(PlatformSettings(key=SCHLUESSEL, value=json.dumps(stand)))
    else:
        zeile.value = json.dumps(stand)
    await db.flush()
    return stand


def zustand(stand: dict, now: datetime | None = None) -> str | None:
    """``attention.BROKEN`` / ``WAITING`` / ``None`` für einen gelesenen Stand."""
    fehler = stand.get("letzter_fehler") or {}
    return attention.backup_state(_zeit(stand.get("zuletzt_ok")), _zeit(fehler.get("zeit")), now)


def ansicht(stand: dict, eingerichtet: bool, now: datetime | None = None) -> dict:
    """Was die Karte „Datensicherung“ braucht — die Ampel als ok/gelb/rot."""
    ampel = {attention.BROKEN: "rot", attention.WAITING: "gelb", None: "ok"}[zustand(stand, now)]
    return {
        "zuletzt_ok": stand.get("zuletzt_ok"),
        "groesse_bytes": stand.get("groesse_bytes"),
        "dauer_s": stand.get("dauer_s"),
        "volumes": stand.get("volumes"),
        "letzter_fehler": stand.get("letzter_fehler"),
        "ampel": ampel,
        "eingerichtet": eingerichtet,
    }
