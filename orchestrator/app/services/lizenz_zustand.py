"""Der Lizenzzustand einer Anlage — laden, merken, berichten (#886).

``core/license.py`` kennt die Regeln und haelt den Zustand im Speicher. Was
einen Neustart ueberleben muss, steht in den Einstellungen; dieses Modul ist die
Bruecke dazwischen, damit Start, Lizenz-API und Lebenszeichen denselben Weg gehen.

Gemerkt wird dreierlei:
  * ``license_last_limit``    Agentenlimit der zuletzt eingetragenen Lizenz —
                              gilt weiter, wenn die Lizenz entfernt wird.
  * ``license_server_status`` was der Lizenzserver zuletzt gemeldet hat.
  * ``license_private_use``   die Erklaerung des Administrators, die Anlage
                              privat / nicht-gewerblich zu nutzen.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as app_settings
from app.core import license as lizenz
from app.core.agentenlimit import agentenzahl


def SettingsService(db):  # noqa: N802 — gleicher Name wie die Klasse, bewusst
    """Die Einstellungen, spaet gebunden.

    Aufrufer (Lebenszeichen, Tests) ersetzen ``settings_service.SettingsService``;
    ein Import auf Modulebene haette hier die alte Klasse festgehalten.
    """
    from app.services import settings_service
    return settings_service.SettingsService(db)

SCHLUESSEL_LIMIT = "license_last_limit"
SCHLUESSEL_SERVER_STATUS = "license_server_status"
SCHLUESSEL_PRIVAT = "license_private_use"


async def lade_lizenzzustand(db: AsyncSession) -> None:
    """Beim Start: Lizenz, gemerktes Limit und Serverstatus in den Speicher holen."""
    svc = SettingsService(db)
    lizenz.load_license_from_string(await svc.get("license_key") or "")
    lizenz.merke_limit(await svc.get(SCHLUESSEL_LIMIT))
    lizenz.setze_server_status(await svc.get(SCHLUESSEL_SERVER_STATUS))


async def lizenz_eintragen(db: AsyncSession, schluessel: str) -> lizenz.License:
    """Eine gepruefte Lizenz uebernehmen. Ihr Limit ersetzt das gemerkte, und was
    der Server zur ALTEN Lizenz gemeldet hat, gilt fuer die neue nicht mehr."""
    lic = lizenz.load_license_from_string(schluessel)
    svc = SettingsService(db)
    await svc.set("license_key", schluessel)
    await svc.set(SCHLUESSEL_LIMIT, str(lic.instance_limit or 0))
    await svc.set(SCHLUESSEL_SERVER_STATUS, "")
    lizenz.merke_limit(lic.instance_limit)
    lizenz.setze_server_status("")
    return lic


async def lizenz_entfernen(db: AsyncSession) -> None:
    """Den Schluessel entfernen. Das gemerkte Limit bleibt bewusst stehen."""
    await SettingsService(db).set("license_key", "")
    lizenz.load_license_from_string("")


async def merke_server_status(db: AsyncSession, status: str | None) -> str:
    """Den vom Lizenzserver gemeldeten Status festhalten; gibt zurueck, was gilt."""
    wert = lizenz.setze_server_status(status)
    await SettingsService(db).set(SCHLUESSEL_SERVER_STATUS, wert)
    return wert


async def setze_private_nutzung(db: AsyncSession, privat: bool) -> None:
    await SettingsService(db).set(SCHLUESSEL_PRIVAT, "true" if privat else "")


async def _tage_seit_einrichtung(db: AsyncSession) -> int | None:
    """Alter der Anlage, gemessen am aeltesten Nutzerkonto. ``None``, wenn es
    noch keines gibt — dann laeuft auch keine Testphase."""
    from app.models.user import User

    erstes = (await db.execute(select(func.min(User.created_at)))).scalar()
    if erstes is None:
        return None
    if erstes.tzinfo is None:
        erstes = erstes.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - erstes).days)


async def lizenzstatus(db: AsyncSession, *, fuer_admin: bool) -> dict:
    """Was die Oberflaeche zum Lizenzstand wissen muss.

    Belegung, Limit und Hinweise gehen nur an Administratoren: sie sind die
    Einzigen, die etwas daran aendern koennen, und fuer alle anderen waere ein
    Lizenzhinweis nur Laerm. Das gilt auch fuer den Hinweis des Anbieters aus
    der Antwort auf das taegliche Lebenszeichen (``betreiber_hinweis``) — er
    enthaelt eine Kontaktadresse und stand frueher im oeffentlichen
    ``/version/`` (#917).
    """
    lic = lizenz.get_current_license()
    status = lic.to_dict()
    if not fuer_admin:
        return status

    limit, quelle = lizenz.wirksames_agentenlimit()
    agenten = await agentenzahl(db)
    einstellungen = SettingsService(db)
    privat = (await einstellungen.get(SCHLUESSEL_PRIVAT) or "").strip().lower() == "true"
    betreiber_hinweis = (await einstellungen.get("usage_ping_hinweis") or "").strip()
    tage = await _tage_seit_einrichtung(db)
    status.update({
        "agenten": agenten,
        "agentenlimit": limit,
        "limit_quelle": quelle,
        "tage_seit_einrichtung": tage,
        "testphase_tage": lizenz.TESTPHASE_TAGE,
        "private_nutzung": privat,
        "betreiber_hinweis": betreiber_hinweis,
        # Editionen, Preise, Kontakt: beim Anbieter der Anlage, nicht im
        # Quellcode-Repository (#899). Einstellbar ueber die .env.
        "anbieter": {"url": app_settings.anbieter_url.strip(),
                     "kontakt": app_settings.anbieter_kontakt.strip()},
        "hinweis": lizenz.lizenz_hinweis(
            zustand=lic.zustand, agenten=agenten, limit=limit, limit_quelle=quelle,
            tage_seit_einrichtung=tage, privat_erklaert=privat,
        ),
    })
    return status
