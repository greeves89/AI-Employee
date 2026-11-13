"""Zugang der Kiosk-Anzeige (Plan Sicherheit 2026-11, Punkt 1).

Der Kiosk (``api/kiosk.py``, nur mit ``KIOSK_ENABLED``) zeigt Status und lässt mit
Agenten chatten, ohne dass sich jemand anmeldet. Bisher schützte ihn nur Caddy vor
dem Cloudflare-Tunnel. Aus dem lokalen Netz und von Agenten, die den Orchestrator
direkt erreichen, war er offen (Sicherheitsreview 1.461).

Jetzt braucht jedes Kiosk-Gerät einen Zugang:
* Ein Admin erzeugt ihn (``erzeugen``) und bekommt einmal einen Link
  ``/kiosk?zugang=<…>``. Gespeichert wird nur der SHA-256-Wert.
* Die Kiosk-Seite tauscht den Link einmal gegen ein Cookie (``COOKIE``, HttpOnly,
  SameSite=Strict, nur für ``/api/v1/kiosk``). Danach schickt der Browser es mit.
* Ohne gültigen Zugang antwortet jeder Kiosk-Endpunkt 404 — wie ein Pfad, den es
  nicht gibt. Ist kein Zugang eingerichtet, ist der Kiosk zu.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request

SCHLUESSEL = "kiosk_zugang_hash"
COOKIE = "aie_kiosk"
KOPFZEILE = "x-kiosk-zugang"


def _wert(zugang: str) -> str:
    return hashlib.sha256(zugang.encode("utf-8")).hexdigest()


async def _gespeichert() -> str:
    from app.db.session import async_session_factory
    from app.services.settings_service import SettingsService

    async with async_session_factory() as db:
        return (await SettingsService(db).get(SCHLUESSEL) or "").strip()


def passt(zugang: str | None, gespeichert: str) -> bool:
    """Gehört ``zugang`` zum gespeicherten Wert? Ohne gespeicherten Wert: nie."""
    if not zugang or not gespeichert or len(zugang) > 200:
        return False
    return hmac.compare_digest(_wert(zugang), gespeichert)


async def pruefen(request: Request) -> None:
    """Abhängigkeit für alle Kiosk-Endpunkte."""
    zugang = request.cookies.get(COOKIE) or request.headers.get(KOPFZEILE)
    if not passt(zugang, await _gespeichert()):
        raise HTTPException(status_code=404, detail="Not Found")


async def erzeugen(db) -> str:
    """Neuen Zugang anlegen (ersetzt einen alten). Gibt den Klartext EINMAL zurück."""
    from app.services.settings_service import SettingsService

    zugang = secrets.token_urlsafe(32)
    await SettingsService(db).set(SCHLUESSEL, _wert(zugang))
    return zugang


async def entfernen(db) -> None:
    from app.services.settings_service import SettingsService

    await SettingsService(db).set(SCHLUESSEL, "")
