"""Zwei-Faktor-Anmeldung (TOTP) — Endpunkte (#915).

Zwei Wege, ein Kern (``core/zwei_faktor``):

- Mit Zwischen-Token aus ``POST /auth/login`` (noch keine Sitzung):
  ``/auth/mfa/verify`` (Code eingeben) und ``/auth/mfa/pending/*`` (Pflicht-
  Einrichtung, wenn der Admin Zwei-Faktor erzwingt und das Konto noch keinen hat).
- Mit Sitzung, in den eigenen Einstellungen: Status, Einrichten, Abschalten.
  Diese Endpunkte kennen KEINE fremde Nutzer-ID — sie wirken immer auf das
  angemeldete Konto. Zurücksetzen für andere: nur Admin, ``/auth/users/{id}/mfa-reset``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _anmeldung_protokollieren, anmeldung_abschliessen
from app.core import zwei_faktor as zf
from app.core.audit import AKTEUR_ANMELDUNG, protokolliere
from app.core.qr_svg import qr_svg
from app.db.session import get_db
from app.models.audit_log import AuditEventType
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/mfa", tags=["auth"])


class MfaTokenRequest(BaseModel):
    mfa_token: str


class MfaVerifyRequest(BaseModel):
    mfa_token: str
    code: str


class MfaCodeRequest(BaseModel):
    code: str


class MfaPasswortRequest(BaseModel):
    password: str


class MfaAbschaltenRequest(BaseModel):
    password: str
    code: str


_UNGUELTIG = "Die Anmeldung ist abgelaufen. Bitte melde dich erneut an."
_KEIN_CACHE = {"Cache-Control": "no-store"}  # Antworten mit Geheimnis oder Wiederherstellungscodes
_FALSCHER_CODE = "Der Code stimmt nicht."


# --- Helfer ---------------------------------------------------------------


async def _nutzer_aus_pending(db: AsyncSession, token: str, zweck: str) -> User:
    """Zwischen-Token prüfen und das Konto laden — sonst 401."""
    try:
        daten = zf.pending_token_pruefen(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail=_UNGUELTIG)
    if daten.get("zweck") != zweck:
        raise HTTPException(status_code=401, detail=_UNGUELTIG)
    user = await db.scalar(select(User).where(User.id == daten["sub"]).with_for_update())
    if (not user or not user.is_active or not getattr(user, "approved", True)
            or daten.get("tv", 0) != user.token_version):
        raise HTTPException(status_code=401, detail=_UNGUELTIG)
    return user


async def _pruefen(request: Request, db: AsyncSession, user: User, code: str, *,
                   geheimnis: str | None = None, wiederherstellung_erlaubt: bool = True,
                   anmeldung: bool) -> str:
    """Zweiten Faktor prüfen und Fehlschläge ins Prüfprotokoll schreiben.

    Gibt die Art (``totp``/``wiederherstellungscode``) zurück oder wirft 401/429/503.
    """
    art = await _mit_sperre(db, user, anmeldung, zf.zweiten_faktor_pruefen(
        zf.redis_aus_anfrage(request), user, code,
        geheimnis=geheimnis, wiederherstellung_erlaubt=wiederherstellung_erlaubt,
    ))
    if art is None:
        await _fehlschlag(db, user, anmeldung, grund="falscher Bestätigungscode")
        # Mit Sitzung 403 statt 401: auf 401 erneuert die Oberfläche die Sitzung und
        # schickt dieselbe Anfrage noch einmal — das wäre ein zweiter Fehlversuch.
        raise HTTPException(status_code=401 if anmeldung else 403, detail=_FALSCHER_CODE)
    return art


async def _mit_sperre(db: AsyncSession, user: User, anmeldung: bool, pruefung):
    """Eine Prüfung unter der gemeinsamen Sperre (Codes UND Passwort) ausführen:
    ohne Redis 503, während der Sperre 429 (mit Prüfprotokoll-Eintrag)."""
    try:
        return await pruefung
    except zf.ZweiFaktorNichtVerfuegbar:
        logger.error("Zwei-Faktor-Prüfung ohne Redis nicht möglich")
        raise HTTPException(status_code=503,
                            detail="Die Prüfung ist gerade nicht möglich. Bitte später erneut versuchen.")
    except zf.ZweiFaktorGesperrt as g:
        await _fehlschlag(db, user, anmeldung, grund="gesperrt nach zu vielen Fehlversuchen")
        minuten = max(1, (g.sekunden + 59) // 60)
        raise HTTPException(status_code=429,
                            detail=f"Zu viele Fehlversuche. Bitte in {minuten} Minuten erneut versuchen.")


async def _fehlschlag(db: AsyncSession, user: User, anmeldung: bool, grund: str) -> None:
    """Nie mit dem Code — nur wer, Weg und Grund."""
    if anmeldung:
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=user.email,
                                        user=user, weg="passwort+code", grund=grund)
        return
    await protokolliere(db, AuditEventType.MFA_CODE_FAILED, agent_id=AKTEUR_ANMELDUNG,
                        user_id=user.id, command=f"Zwei-Faktor-Code abgelehnt: {user.email}",
                        outcome="failure", meta={"email": user.email, "grund": grund})
    await db.commit()


async def _ereignis(db: AsyncSession, ereignis: AuditEventType, user: User, **meta) -> None:
    await protokolliere(db, ereignis, agent_id=AKTEUR_ANMELDUNG, user_id=user.id,
                        command=f"{ereignis.value}: {user.email}",
                        meta={"email": user.email, **{k: v for k, v in meta.items() if v is not None}})


def _einrichtung_beginnen(user: User) -> dict:
    """Neues Geheimnis verschlüsselt ablegen (noch NICHT aktiv) und für die App ausgeben."""
    if not user.password_hash:
        # Reine SSO-Konten melden sich nie per Passwort an — der zweite Faktor
        # liegt beim Identitätsanbieter. Ein TOTP hier wäre Schein-Sicherheit.
        raise HTTPException(status_code=400,
                            detail="Dieses Konto meldet sich über SSO an — den zweiten Faktor verwaltet dein Identitätsanbieter.")
    if zf.mfa_aktiv(user):
        raise HTTPException(status_code=409, detail="Zwei-Faktor ist bereits eingerichtet.")
    geheimnis = zf.neues_geheimnis()
    user.totp_secret_encrypted = zf.geheimnis_verschluesseln(geheimnis)
    user.mfa_enabled_at = None
    uri = zf.otpauth_uri(geheimnis, user.email)
    return {"secret": geheimnis, "otpauth_uri": uri, "qr_svg": qr_svg(uri, groesse=200)}


async def _einrichtung_abschliessen(request: Request, db: AsyncSession, user: User, code: str) -> list[str]:
    """Code zum begonnenen Geheimnis prüfen, aktivieren, Wiederherstellungscodes ausgeben."""
    if zf.mfa_aktiv(user):
        raise HTTPException(status_code=409, detail="Zwei-Faktor ist bereits eingerichtet.")
    geheimnis = zf.geheimnis_entschluesseln(user)
    if not geheimnis:
        raise HTTPException(status_code=400, detail="Bitte die Einrichtung zuerst starten.")
    await _pruefen(request, db, user, code, geheimnis=geheimnis,
                   wiederherstellung_erlaubt=False, anmeldung=False)
    codes, ablage = zf.wiederherstellungscodes_erzeugen()
    user.mfa_recovery_codes = ablage
    user.mfa_enabled_at = datetime.now(timezone.utc)
    await _ereignis(db, AuditEventType.MFA_ENABLED, user)
    await db.commit()
    return codes


# --- Mit Zwischen-Token (Anmeldung) ----------------------------------------


@router.post("/verify")
async def verify(body: MfaVerifyRequest, request: Request, response: Response,
                 db: AsyncSession = Depends(get_db)):
    """Zweiter Schritt der Anmeldung: Code aus der App oder Wiederherstellungscode."""
    user = await _nutzer_aus_pending(db, body.mfa_token, zf.ZWECK_CODE)
    if not zf.mfa_aktiv(user):
        raise HTTPException(status_code=401, detail=_UNGUELTIG)
    art = await _pruefen(request, db, user, body.code, anmeldung=True)
    weg = "passwort+wiederherstellungscode" if art == "wiederherstellungscode" else "passwort+totp"
    ergebnis = await anmeldung_abschliessen(user, request, response, db, weg=weg)
    if art == "wiederherstellungscode":
        ergebnis["recovery_codes_left"] = zf.verbleibende_codes(user)
    return ergebnis


@router.post("/pending/setup")
async def pending_einrichtung_starten(body: MfaTokenRequest, response: Response,
                                      db: AsyncSession = Depends(get_db)):
    """Pflicht-Einrichtung direkt nach dem Passwort — noch ohne Sitzung."""
    user = await _nutzer_aus_pending(db, body.mfa_token, zf.ZWECK_EINRICHTEN)
    daten = _einrichtung_beginnen(user)
    await db.commit()
    response.headers.update(_KEIN_CACHE)
    return daten


@router.post("/pending/confirm")
async def pending_einrichtung_bestaetigen(body: MfaVerifyRequest, request: Request, response: Response,
                                          db: AsyncSession = Depends(get_db)):
    """Pflicht-Einrichtung bestätigen — danach ist die Anmeldung vollständig."""
    user = await _nutzer_aus_pending(db, body.mfa_token, zf.ZWECK_EINRICHTEN)
    codes = await _einrichtung_abschliessen(request, db, user, body.code)
    ergebnis = await anmeldung_abschliessen(user, request, response, db, weg="passwort+totp")
    ergebnis["recovery_codes"] = codes
    response.headers.update(_KEIN_CACHE)
    return ergebnis


# --- Mit Sitzung (eigene Einstellungen) ------------------------------------


async def _ich(request: Request, db: AsyncSession) -> User:
    from app.dependencies import get_current_user

    ich = await get_current_user(request, db)
    # Frisch und gesperrt laden: das Objekt aus der Anmeldung kann aus einer
    # anderen Sitzung stammen, und Einlösen/Abschalten darf nicht doppelt laufen.
    user = await db.scalar(select(User).where(User.id == ich.id).with_for_update())
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    user = await _ich(request, db)
    return {
        "enabled": zf.mfa_aktiv(user),
        "enabled_at": user.mfa_enabled_at.isoformat() if user.mfa_enabled_at else None,
        "required": zf.pflicht_fuer(user),
        "password_account": bool(user.password_hash),
        "recovery_codes_left": zf.verbleibende_codes(user) if zf.mfa_aktiv(user) else 0,
    }


async def _passwort_bestaetigen(request: Request, db: AsyncSession, user: User, passwort: str,
                                grund: str) -> None:
    """Eine offene Sitzung allein reicht für Änderungen am zweiten Faktor nicht:
    wer nur ein gestohlenes Sitzungstoken hat, kennt das Passwort nicht — und
    darf es auch nicht ungebremst raten (dieselbe Sperre wie für Codes)."""
    ok = await _mit_sperre(db, user, False,
                           zf.passwort_pruefen(zf.redis_aus_anfrage(request), user, passwort))
    if not ok:
        await _fehlschlag(db, user, anmeldung=False, grund=grund)
        # 403, nicht 401 — siehe _pruefen.
        raise HTTPException(status_code=403, detail="Das Passwort stimmt nicht.")


@router.post("/setup")
async def einrichten_starten(body: MfaPasswortRequest, request: Request, response: Response,
                             db: AsyncSession = Depends(get_db)):
    """Einrichtung beginnen — nur mit dem aktuellen Passwort."""
    user = await _ich(request, db)
    if user.password_hash:
        await _passwort_bestaetigen(request, db, user, body.password, "falsches Passwort beim Einrichten")
    daten = _einrichtung_beginnen(user)
    await db.commit()
    response.headers.update(_KEIN_CACHE)
    return daten


@router.post("/confirm")
async def einrichten_bestaetigen(body: MfaCodeRequest, request: Request, response: Response,
                                 db: AsyncSession = Depends(get_db)):
    user = await _ich(request, db)
    codes = await _einrichtung_abschliessen(request, db, user, body.code)
    response.headers.update(_KEIN_CACHE)
    return {"enabled": True, "recovery_codes": codes}


@router.post("/disable")
async def abschalten(body: MfaAbschaltenRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Nur mit Passwort UND gültigem Code — eine offene Sitzung allein reicht nicht."""
    user = await _ich(request, db)
    if not zf.mfa_aktiv(user):
        raise HTTPException(status_code=409, detail="Zwei-Faktor ist nicht eingerichtet.")
    await _passwort_bestaetigen(request, db, user, body.password, "falsches Passwort beim Abschalten")
    if not (body.code or "").strip():
        raise HTTPException(status_code=400, detail="Bitte den Code aus der App eingeben.")
    await _pruefen(request, db, user, body.code, anmeldung=False)
    user.totp_secret_encrypted = None
    user.mfa_enabled_at = None
    user.mfa_recovery_codes = None
    await _ereignis(db, AuditEventType.MFA_DISABLED, user, pflicht=zf.pflicht_fuer(user) or None)
    await db.commit()
    return {"enabled": False}
