"""Authentication API endpoints: register, login, logout, user management."""

import json
import logging
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field, computed_field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.permissions import role_for_new_user
from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.audit import AKTEUR_ANMELDUNG, protokolliere
from app.core.log_redaction import scrub_log
from app.core.passwort_regeln import passwort_pruefen
from app.db.session import get_db
from app.models.audit_log import AuditEventType
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# --- Login brute-force protection ---
# Je Konto UND Absender-Adresse (#908, Sicherheitspruefung v1.362.0): eine Sperre
# nur je Konto liesse sich von aussen gegen ein fremdes Konto richten — wer von
# einer anderen Adresse kommt, bleibt unberuehrt. Ohne eindeutige Adresse (hinter
# einem Proxy) gilt die Grenze je Konto wie bisher.
#
# Dazu EINE Obergrenze je Konto ueber alle Adressen: die Absender-Adresse ist
# hinter einem Proxy faelschbar (uvicorn vertraut X-Forwarded-For), mit
# wechselnden Adressen liefe die Grenze je Konto+Adresse sonst ins Leere — fuer
# das Raten eines Passworts wie fuer das Fluten des Pruefprotokolls. Grosszuegiger
# als die Grenze je Adresse, damit niemand mit fuenf Fehlversuchen ein fremdes
# Konto sperrt.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300  # 5 minutes
_LOGIN_MAX_ATTEMPTS_KONTO = 20
_LOGIN_WINDOW_SECONDS_KONTO = 900  # 15 Minuten
_ALLE_ADRESSEN = "*"
_login_attempts: dict[str, list[float]] = defaultdict(list)


def adresse_normalisieren(email: str | None) -> str:
    """Schreibvarianten einer E-Mail-Adresse auf eine Form bringen (Unicode-NFKC,
    Leerzeichen, Gross-/Kleinschreibung) — fuer Grenzen und die Kontozuordnung."""
    import unicodedata

    return unicodedata.normalize("NFKC", email or "").strip().lower()


def _login_key(email: str, ip: str | None) -> str:
    return f"{adresse_normalisieren(email)}|{ip or ''}"


def _check_login_rate(email: str, ip: str | None = None) -> None:
    """Sperrt nach zu vielen Fehlversuchen — je Konto+Adresse und je Konto insgesamt."""
    now = time.time()
    for key, maximum, fenster in (
        (_login_key(email, ip), _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SECONDS),
        (_login_key(email, _ALLE_ADRESSEN), _LOGIN_MAX_ATTEMPTS_KONTO, _LOGIN_WINDOW_SECONDS_KONTO),
    ):
        _login_attempts[key] = [t for t in _login_attempts[key] if now - t < fenster]
        if len(_login_attempts[key]) >= maximum:
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Anmeldeversuche. Bitte in {fenster // 60} Minuten erneut versuchen.",
            )


def _record_failed_login(email: str, ip: str | None = None) -> None:
    jetzt = time.time()
    _login_attempts[_login_key(email, ip)].append(jetzt)
    if ip != _ALLE_ADRESSEN:
        _login_attempts[_login_key(email, _ALLE_ADRESSEN)].append(jetzt)


def _clear_login_attempts(email: str, ip: str | None = None) -> None:
    """Ohne ``ip``: alle Zaehler dieser Adresse (Tests, Verwaltung)."""
    if ip is not None:
        _login_attempts.pop(_login_key(email, ip), None)
        return
    praefix = f"{adresse_normalisieren(email)}|"
    for key in [k for k in _login_attempts if k.startswith(praefix)]:
        _login_attempts.pop(key, None)

# Cookie config
COOKIE_ACCESS = "access_token"
COOKIE_REFRESH = "refresh_token"
_is_https = settings.oauth_redirect_base_url.startswith("https://")
COOKIE_OPTS: dict = {
    "httponly": True,
    "samesite": "lax",
    "secure": _is_https,
    "path": "/",
}


# --- Schemas ---


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


#: Einzige Anmeldeart mit Profilfoto (Microsoft Graph).
FOTO_ANBIETER = "microsoft"
#: Inhaltsarten, die ``/me/photo`` durchreicht — Rasterbilder, kein SVG.
FOTO_INHALTSARTEN = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    custom_role_id: int | None = None
    is_active: bool
    approved: bool = True
    last_active_at: datetime | None = None
    monthly_cost_usd: float = 0.0
    tutorial_seen_at: datetime | None = None
    #: Nur zum Ableiten von ``has_photo_source`` gelesen, nie ausgeliefert.
    sso_provider: str | None = Field(default=None, exclude=True)
    #: Zwei-Faktor eingerichtet (#915) — für die Nutzerverwaltung und die eigene Ansicht.
    mfa_enabled: bool = False

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def has_photo_source(self) -> bool:
        """Gibt es eine Quelle fuer ``/auth/me/photo``? (#907)

        Ohne Quelle fragt die Oberflaeche gar nicht erst nach — vorher stand bei
        jedem Laden eine 404 in der Konsole.
        """
        return self.sso_provider == FOTO_ANBIETER


class UserUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    approved: bool | None = None
    #: Einzelfreigabe fuer ein eigenes Claude-/Codex-Abo. Kundenvorgabe vom
    #: 18.08.2026: generell unterbinden, einzelne Nutzer manuell freischalten.
    allow_personal_credentials: bool | None = None


# --- Helpers ---


def _set_auth_cookies(response: Response, user: User) -> dict:
    access = create_access_token(user.id, user.role.value, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    response.set_cookie(COOKIE_ACCESS, access, max_age=1800, **COOKIE_OPTS)
    response.set_cookie(COOKIE_REFRESH, refresh, max_age=604800, **COOKIE_OPTS)
    return {"access_token": access}


async def _anmeldung_protokollieren(
    db: AsyncSession, ereignis: AuditEventType, *, email: str, user: User | None = None,
    weg: str = "passwort", grund: str | None = None, befehl: str | None = None,
    zusatz: dict | None = None,
) -> bool:
    """Anmeldeereignis festschreiben (#908). NIE mit Passwort — nur E-Mail, Weg, Grund.

    Eigenes Festschreiben, weil ein Fehllogin danach mit einer Ausnahme endet:
    ohne Commit ginge der Eintrag mit der Sitzung verloren. Gibt zurueck, ob der
    Eintrag wirklich steht (die Sammel-Protokollierung haengt daran).
    """
    meta = {"email": email, "weg": weg}
    if grund:
        meta["grund"] = grund
    meta.update(zusatz or {})
    eintrag = await protokolliere(
        db, ereignis, agent_id=AKTEUR_ANMELDUNG,
        user_id=getattr(user, "id", None),
        command=befehl or f"{'Abmeldung' if ereignis == AuditEventType.LOGOUT else 'Anmeldung'} ({weg})",
        outcome="failure" if ereignis == AuditEventType.LOGIN_FAILED else "success",
        meta=meta,
    )
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — Protokoll darf die Anmeldung nicht verhindern
        logger.warning("Anmeldeereignis konnte nicht gespeichert werden", exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False
    return eintrag is not None


async def _konto_zur_adresse(db: AsyncSession, email: str | None) -> User | None:
    """Das Konto hinter einer Schreibvariante (Gross-/Kleinschreibung, Leerzeichen,
    Unicode-Formen) — damit ein Angriff auf ein BESTEHENDES Konto nie in der
    Sammelzaehlung fuer unbekannte Adressen verschwindet."""
    form = adresse_normalisieren(email)
    if not form:
        return None
    return (await db.execute(
        select(User).where(func.lower(func.trim(User.email)) == form).limit(1)
    )).scalars().first()


async def _fehlanmeldung_protokollieren(
    db: AsyncSession, request: Request, *, email: str, user: User | None,
    weg: str = "passwort", grund: str | None = None,
) -> None:
    """Fehlanmeldung festschreiben, ohne dass erfundene Adressen das Protokoll fluten.

    Bestehende Konten (auch in anderer Schreibweise): jeder Fehlversuch ein
    Eintrag. Unbekannte Adressen: gezaehlt je Stunde und Weg — ein Eintrag,
    sobald einer fuer die Stunde faellig ist (und erst als erledigt vermerkt, wenn
    er wirklich steht), Zwischenstaende bei 10, 100, 1000 … und die Zusammenfassung
    der vorigen Stunde (core/anmelde_drossel).
    """
    if user is None:
        user = await _konto_zur_adresse(db, email)
    if user is not None:
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=email, user=user,
                                        weg=weg, grund=grund)
        return
    from app.core import anmelde_drossel
    from app.core.zwei_faktor import redis_aus_anfrage

    redis = redis_aus_anfrage(request)
    z = await anmelde_drossel.unbekannte_zaehlen(redis, weg)
    if z.vorige:
        if await _anmeldung_protokollieren(
            db, AuditEventType.LOGIN_FAILED, email="", weg=weg,
            befehl=f"{z.vorige} Fehlanmeldungen mit unbekannter Adresse in der vorigen Stunde ({weg})",
            zusatz={"anzahl": z.vorige, "zusammenfassung": True, "unbekannt": True},
        ):
            await anmelde_drossel.zusammenfassung_merken(redis, weg, z.stunde - 1)
    if not z.melden:
        return
    if z.anzahl == 1:
        befehl = (f"Fehlanmeldung mit unbekannter Adresse ({weg}) — weitere in dieser "
                  "Stunde werden nur gezählt")
    else:
        befehl = f"{z.anzahl} Fehlanmeldungen mit unbekannter Adresse in dieser Stunde ({weg})"
    if await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=email, weg=weg,
                                       grund=grund, befehl=befehl,
                                       zusatz={"anzahl": z.anzahl, "unbekannt": True}):
        await anmelde_drossel.gemeldet_merken(redis, weg, z.stunde)


async def _sso_fehler_protokollieren(db: AsyncSession, request: Request, provider: str,
                                     fehler: Exception) -> None:
    """OIDC-Rueckruf gescheitert. Hat der Anbieter schon jemanden bestaetigt (Konto
    deaktiviert, Registrierung zu …), ist das ein echtes Ereignis: einzeln. Davor
    (ungueltiger state, Code-Tausch) laesst es sich von aussen beliebig ausloesen:
    gesammelt wie unbekannte Adressen — aber nie stumm."""
    from app.services.sso_service import vor_identitaet

    weg, grund = f"sso:{provider}", str(fehler)[:200]
    if vor_identitaet(fehler):
        await _fehlanmeldung_protokollieren(db, request, email="", user=None, weg=weg, grund=grund)
    else:
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email="", weg=weg, grund=grund)


def _drossel_ip(request: Request) -> str | None:
    from app.core.client_ip import eindeutige_client_ip

    return eindeutige_client_ip(request)


async def ip_grenze_pruefen(request: Request) -> None:
    """429, wenn von DIESER Adresse zu viele Fehlversuche kamen (Anmeldung + Code).
    Ohne eindeutige Adresse keine Sperre (core/client_ip)."""
    from app.core import anmelde_drossel
    from app.core.zwei_faktor import redis_aus_anfrage

    rest = await anmelde_drossel.ip_gesperrt_fuer(redis_aus_anfrage(request), _drossel_ip(request))
    if rest:
        minuten = max(1, (rest + 59) // 60)
        raise HTTPException(status_code=429,
                            detail=f"Zu viele Fehlversuche. Bitte in {minuten} Minuten erneut versuchen.")


async def ip_fehlversuch_merken(request: Request) -> None:
    from app.core import anmelde_drossel
    from app.core.zwei_faktor import redis_aus_anfrage

    await anmelde_drossel.ip_fehlversuch(redis_aus_anfrage(request), _drossel_ip(request))


async def _verwaltung(db: AsyncSession, ereignis: AuditEventType, admin, ziel: User | None, **meta):
    """Verwaltungsschritt an einem Nutzer vormerken — wer hat was an wem geaendert."""
    daten = {k: v for k, v in meta.items() if v is not None}
    if ziel is not None:
        daten.setdefault("target_user_id", ziel.id)
        daten.setdefault("target_email", ziel.email)
    return await protokolliere(
        db, ereignis, user_id=getattr(admin, "id", None),
        command=f"{ereignis.value}: {ziel.email}" if ziel is not None else ereignis.value,
        meta=daten,
    )


# --- Public Endpoints ---


class SetupRegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    setup_token: str | None = None


@router.post("/register")
async def register(body: SetupRegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    # Check if this is the first user (auto-admin)
    count = await db.scalar(select(func.count()).select_from(User))
    is_first = count == 0

    # Setup-Mode protection: first admin registration requires SETUP_TOKEN
    # if one is configured. This prevents anyone who finds the URL from
    # becoming admin on an uninitialized instance.
    if is_first and settings.setup_token:
        if body.setup_token != settings.setup_token:
            raise HTTPException(
                status_code=403,
                detail="Für das erste Administratorkonto wird der Einrichtungsschlüssel "
                "benötigt (SETUP_TOKEN aus der .env-Datei).",
            )

    # If not first user, check if registration is open
    if not is_first and not settings.registration_open:
        raise HTTPException(status_code=403, detail="Die Registrierung ist geschlossen.")

    # Check duplicate email
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(status_code=409, detail="Diese E-Mail-Adresse ist bereits registriert.")

    # Passwortregeln — dieselben wie bei Anlage und Zuruecksetzen (core/passwort_regeln).
    passwort_pruefen(body.password, body.email)

    approved = is_first or not settings.require_user_approval
    user = User(
        id=uuid.uuid4().hex[:12],
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=role_for_new_user(is_first),
        approved=approved,
    )
    db.add(user)
    await protokolliere(
        db, AuditEventType.USER_CREATED, user_id=user.id,
        command=f"Selbstregistrierung: {user.email}",
        meta={"weg": "registrierung", "target_user_id": user.id, "target_email": user.email,
              "role": user.role.value, "approved": approved, "erster_nutzer": is_first},
    )
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered: {user.email} (role: {user.role.value}, first: {is_first}, approved: {approved})")

    # Pending approval → no session; the frontend shows a "wait for admin" notice.
    if not approved:
        return {"pending": True, "user": UserResponse.model_validate(user).model_dump()}

    # Zwei-Faktor-Pflicht (#915) gilt auch hier: wie beim Login keine Sitzung,
    # sondern das Zwischen-Token für die Pflicht-Einrichtung.
    from app.core import zwei_faktor
    if zwei_faktor.pflicht_fuer(user):
        return {
            "mfa_required": True,
            "mfa_setup_required": True,
            "mfa_token": zwei_faktor.pending_token_erstellen(user, zwei_faktor.ZWECK_EINRICHTEN),
        }

    tokens = _set_auth_cookies(response, user)
    return {
        "user": UserResponse.model_validate(user).model_dump(),
        **tokens,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    # SSO-only mode: password login disabled (only Microsoft SSO + MFA). The env
    # break-glass (EMERGENCY_PASSWORD_LOGIN) re-enables it for lockout recovery.
    if settings.sso_only_login and not settings.emergency_password_login:
        raise HTTPException(status_code=403, detail="Die Anmeldung mit Passwort ist abgeschaltet — bitte über Microsoft anmelden.")

    # Brute-force protection: je Absender-IP (Fehlversuche ueber alle Adressen,
    # #908) und je E-Mail-Adresse
    await ip_grenze_pruefen(request)
    absender = _drossel_ip(request)
    _check_login_rate(body.email, absender)

    user = await db.scalar(select(User).where(User.email == body.email))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        _record_failed_login(body.email, absender)
        await ip_fehlversuch_merken(request)
        await _fehlanmeldung_protokollieren(
            db, request, email=body.email, user=user,
            grund="unbekannte E-Mail" if not user else "falsches Passwort",
        )
        raise HTTPException(status_code=401, detail="E-Mail-Adresse oder Passwort ist falsch.")

    if not user.is_active:
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=body.email,
                                        user=user, grund="Konto deaktiviert")
        raise HTTPException(status_code=403, detail="Dieses Konto ist deaktiviert.")
    if not getattr(user, "approved", True):
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=body.email,
                                        user=user, grund="Freischaltung ausstehend")
        raise HTTPException(status_code=403, detail="Dein Konto wartet noch auf Freischaltung durch einen Administrator.")

    _clear_login_attempts(body.email, absender)

    # Zweiter Faktor (#915): Passwort richtig heißt noch nicht drin. Statt der
    # Sitzung gibt es ein kurzlebiges Zwischen-Token, das NUR für den Code-Schritt
    # bzw. die Pflicht-Einrichtung taugt — kein Cookie, kein Zugangstoken.
    from app.core import zwei_faktor

    schritt = zwei_faktor.naechster_schritt(
        sso_anmeldung=False, passwort_ok=True,
        mfa_aktiv=zwei_faktor.mfa_aktiv(user), erzwungen=zwei_faktor.pflicht_fuer(user),
    )
    if schritt in (zwei_faktor.Schritt.CODE, zwei_faktor.Schritt.EINRICHTEN):
        einrichten = schritt == zwei_faktor.Schritt.EINRICHTEN
        zweck = zwei_faktor.ZWECK_EINRICHTEN if einrichten else zwei_faktor.ZWECK_CODE
        return {
            "mfa_required": True,
            "mfa_setup_required": einrichten,
            "mfa_token": zwei_faktor.pending_token_erstellen(user, zweck),
        }

    return await anmeldung_abschliessen(user, request, response, db)


async def anmeldung_abschliessen(user: User, request: Request, response: Response,
                                 db: AsyncSession, weg: str = "passwort") -> dict:
    """Sitzung herstellen — die gemeinsame Endstrecke der Passwort-Anmeldung, mit
    oder ohne zweiten Faktor (#915). Cookies, Protokoll, Aktivität, Agenten wecken."""
    tokens = _set_auth_cookies(response, user)
    await _anmeldung_protokollieren(db, AuditEventType.LOGIN_SUCCEEDED, email=user.email,
                                    user=user, weg=weg)

    # Update activity + wake user's agents (fire-and-forget)
    from app.services.user_lifecycle import wake_user_agents
    user.last_active_at = datetime.now(timezone.utc)
    await db.commit()
    try:
        docker_service = request.app.state.docker
        woken = await wake_user_agents(db, docker_service, user.id)
        if woken:
            logger.info(f"Woke {len(woken)} agents for user {user.email} on login")
    except Exception as e:
        logger.warning(f"Agent wake-up failed on login: {e}")

    return {
        "user": UserResponse.model_validate(user).model_dump(),
        **tokens,
    }


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    # Optional: drop the user's stored MS Graph token on logout (no persistent
    # token after sign-out). Best-effort — never block logout on failure.
    if settings.revoke_msgraph_on_logout:
        try:
            from app.dependencies import get_current_user
            from app.services.oauth_service import OAuthService
            user = await get_current_user(request, db)
            await OAuthService(db, request.app.state.redis).disconnect("microsoft", user_id=user.id)
            logger.info(f"Revoked MS Graph token on logout for {user.email}")
        except Exception as e:
            logger.warning(f"MS token revoke on logout skipped: {e}")
    # Abmeldung protokollieren — nur wenn die Sitzung noch einer Person zuzuordnen
    # ist. Eine abgelaufene Sitzung abzumelden ist kein Ereignis.
    try:
        from app.dependencies import get_current_user
        abmelder = await get_current_user(request, db)
    except Exception:  # noqa: BLE001
        abmelder = None
    if abmelder is not None:
        await _anmeldung_protokollieren(db, AuditEventType.LOGOUT, email=abmelder.email,
                                        user=abmelder, weg="sitzung")
    response.delete_cookie(COOKIE_ACCESS, path="/")
    response.delete_cookie(COOKIE_REFRESH, path="/")
    return {"ok": True}


@router.post("/refresh")
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Refresh access token using the refresh cookie (web), or an ``Authorization:
    Bearer <refresh_token>`` header as a fallback (native app after SSO login — its
    tokens arrive via the app's custom URL scheme, never as a cookie, see
    finish_sso_login)."""
    token = request.cookies.get(COOKIE_REFRESH)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")

    user = await db.scalar(select(User).where(User.id == payload["sub"]))
    if not user or not user.is_active or not getattr(user, "approved", True):
        raise HTTPException(status_code=401, detail="User not found, inactive, or pending approval")
    if payload.get("tv", 0) != user.token_version:
        raise HTTPException(status_code=401, detail="Session revoked")

    tokens = _set_auth_cookies(response, user)
    return {"user": UserResponse.model_validate(user).model_dump(), **tokens}


@router.get("/registration-status")
async def registration_status(db: AsyncSession = Depends(get_db)):
    """Public: check if registration is open and if setup is needed."""
    count = await db.scalar(select(func.count()).select_from(User))
    return {
        "registration_open": settings.registration_open or count == 0,
        "needs_setup": count == 0,
        "setup_token_required": count == 0 and bool(settings.setup_token),
    }


# --- SSO / OIDC Endpoints ---


@router.get("/sso/providers")
async def list_sso_providers(db: AsyncSession = Depends(get_db)):
    """Public: list available SSO providers (only those with configured credentials)."""
    from app.core.sso_providers import SSO_PROVIDERS, is_sso_available

    providers = []
    for name, provider in SSO_PROVIDERS.items():
        if is_sso_available(provider):
            providers.append({
                "name": provider.name,
                "display_name": provider.display_name,
                "icon": provider.icon,
            })

    # SAML steht in derselben Liste wie die OIDC-Anbieter, damit die Anmeldeseite
    # nichts ueber die Protokolle wissen muss. Es erscheint nur, wenn die Angaben
    # vollstaendig sind — sonst fuehrte der Knopf sicher in einen Fehler.
    try:
        from app.core import saml_config

        saml_cfg = await saml_config.load_settings(db)
        if saml_config.is_configured(saml_cfg):
            providers.append({
                "name": saml_config.PROVIDER_NAME,
                "display_name": saml_cfg.get(saml_config.DISPLAY_NAME_SETTING) or "SAML",
                "icon": "key",
            })
    except Exception as e:  # noqa: BLE001 — SAML darf die Anmeldeseite nie blockieren
        logger.warning("SAML-Anbieter konnte nicht geprueft werden: %s", e)
    # sso_only: tells the login page to hide the password form (SSO + MFA only).
    # The env break-glass still allows password login server-side for recovery.
    sso_only = bool(settings.sso_only_login and not settings.emergency_password_login and providers)
    return {"providers": providers, "sso_only": sso_only}


def safe_internal_path(path: str | None) -> str:
    """Return ``path`` if it is a safe same-origin target, else "".

    Only a single leading slash followed by a non-slash, non-backslash character is
    accepted — that rejects "//evil.com" and "/\\evil.com" (protocol-relative open
    redirects) as well as absolute URLs. Same rule the login page applies client-side.
    """
    p = (path or "").strip()
    if not p or len(p) > 2000:
        return ""
    if not p.startswith("/") or p[1:2] in ("/", "\\"):
        return ""
    return p


# --- SAML 2.0 ------------------------------------------------------------------
# Anderes Protokoll, gleicher Rest: die Nutzeraufloesung laeuft ueber dasselbe
# SSOService._find_or_create_user wie bei OIDC, die Sitzung ueber dieselbe
# finish_sso_login. Nur der Weg, auf dem die Identitaet ankommt, ist ein anderer.


def _saml_request_dict(request: Request, form: dict | None = None) -> dict:
    """Die Anfrage in der Form, die python3-saml erwartet.

    ``https`` wird aus der konfigurierten oeffentlichen Adresse abgeleitet, nicht aus
    dem Schema, mit dem die Anfrage beim Prozess ankommt — hinter dem Reverse-Proxy
    ist das intern immer http, und der Identitaetsanbieter wuerde die daraus gebaute
    Zieladresse dann zu Recht ablehnen.
    """
    from urllib.parse import urlparse

    public = urlparse(settings.oauth_redirect_base_url)
    return {
        "https": "on" if public.scheme == "https" else "off",
        "http_host": public.netloc or request.url.hostname or "",
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": form or {},
    }


async def _saml_auth(request: Request, db: AsyncSession, form: dict | None = None):
    """Ein vorbereitetes SAML-Objekt, oder ``None`` wenn nichts konfiguriert ist."""
    from app.core import saml_config

    cfg = await saml_config.load_settings(db)
    if not saml_config.is_configured(cfg):
        return None, cfg
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError:
        # Getrennt gefangen und laut gemeldet: das ist ein Installationsfehler
        # (fehlendes libxmlsec1 im Image), kein Betriebszustand.
        logger.error("SAML ist konfiguriert, aber python3-saml/xmlsec fehlt im Image")
        return None, cfg
    saml_settings = saml_config.build_saml_settings(cfg, settings.oauth_redirect_base_url)
    return OneLogin_Saml2_Auth(_saml_request_dict(request, form), saml_settings), cfg


@router.get("/sso/saml/metadata")
async def saml_metadata(request: Request, db: AsyncSession = Depends(get_db)):
    """Unsere Dienstanbieter-Metadaten — die traegt der Administrator beim
    Identitaetsanbieter ein."""
    from fastapi.responses import Response as RawResponse

    auth_obj, _cfg = await _saml_auth(request, db)
    if auth_obj is None:
        raise HTTPException(status_code=503, detail="SAML ist nicht konfiguriert")
    saml_settings = auth_obj.get_settings()
    metadata = saml_settings.get_sp_metadata()
    errors = saml_settings.validate_metadata(metadata)
    if errors:
        raise HTTPException(status_code=500, detail=f"Metadaten fehlerhaft: {errors}")
    return RawResponse(content=metadata, media_type="application/xml")


@router.get("/sso/saml/login")
async def saml_login(
    request: Request,
    return_to: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Zum Identitaetsanbieter weiterleiten."""
    auth_obj, _cfg = await _saml_auth(request, db)
    if auth_obj is None:
        raise HTTPException(status_code=503, detail="SAML ist nicht konfiguriert")
    # Nur ein geprueftes internes Ziel wird mitgegeben — dieselbe Pruefung wie beim
    # OIDC-Weg, sonst waere das eine offene Weiterleitung.
    target = safe_internal_path(return_to)
    return RedirectResponse(url=auth_obj.login(return_to=target or None), status_code=302)


@router.post("/sso/saml/acs")
async def saml_acs(request: Request, db: AsyncSession = Depends(get_db)):
    """Antwort des Identitaetsanbieters entgegennehmen und anmelden.

    Die Pruefung der Signatur macht python3-saml. Wir werten NUR aus, was die
    Bibliothek als gueltig bestaetigt hat — jeder Zugriff auf die Attribute vor
    ``is_authenticated`` waere ein Einfallstor.
    """
    from app.core import saml_config
    from app.services.sso_service import SSOService

    frontend_url = settings.oauth_redirect_base_url
    if frontend_url.endswith(":8000"):
        frontend_url = frontend_url.replace(":8000", ":3000")

    form = dict(await request.form())
    auth_obj, cfg = await _saml_auth(request, db, form)
    if auth_obj is None:
        return RedirectResponse(url=f"{frontend_url}/login?error=saml_not_configured")

    auth_obj.process_response()
    errors = auth_obj.get_errors()
    if errors or not auth_obj.is_authenticated():
        reason = auth_obj.get_last_error_reason() or ",".join(errors)
        logger.warning("SAML-Antwort abgelehnt: %s", scrub_log(str(reason)))
        return RedirectResponse(url=f"{frontend_url}/login?error=saml_invalid")

    attributes = auth_obj.get_attributes() or {}
    name_id = auth_obj.get_nameid() or ""
    email, name = saml_config.extract_identity(attributes, name_id)
    if not email:
        logger.warning("SAML-Antwort ohne E-Mail-Attribut — Anmeldung nicht moeglich")
        return RedirectResponse(url=f"{frontend_url}/login?error=saml_no_email")

    sso_service = SSOService(db, request.app.state.redis)
    try:
        # Derselbe Weg wie bei OIDC. Die E-Mail gilt als bestaetigt: sie kommt aus
        # einer signierten Assertion des Identitaetsanbieters, nicht aus einer
        # Eingabe des Anmeldenden.
        user = await sso_service._find_or_create_user(
            provider_name=saml_config.PROVIDER_NAME,
            subject=name_id or email,
            email=email,
            name=name or email.split("@")[0],
            email_verified=True,
        )
    except ValueError as e:
        logger.warning("SAML-Anmeldung fehlgeschlagen: %s", scrub_log(str(e)))
        # Einzeln: der Fehler kommt erst nach einer signierten Assertion des
        # Identitaetsanbieters — von aussen nicht beliebig ausloesbar.
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=email,
                                        weg=f"sso:{saml_config.PROVIDER_NAME}", grund=str(e)[:200])
        return RedirectResponse(url=f"{frontend_url}/login?error={e}")

    groups = saml_config.extract_groups(attributes, cfg)
    await sso_service.apply_group_role(user, saml_config.PROVIDER_NAME, groups)

    relay = form.get("RelayState") or ""
    return await finish_sso_login(user, relay, saml_config.PROVIDER_NAME, frontend_url, db=db)


#: Custom URL scheme the native iOS app registers to catch the final SSO redirect.
#: Only ever used when a login's state record was started with ``client=ios`` — see
#: SSOService.generate_login_url / peek_state_client.
IOS_SSO_CALLBACK_URL = "aiemployee://sso-callback"


@router.get("/sso/{provider}/login")
async def sso_login(
    provider: str,
    request: Request,
    redirect: str = Query(None),
    client: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Redirect user to SSO provider for authentication.

    ``redirect`` is an optional internal path to land on after a successful login
    (instead of the dashboard). The MS-Graph MCP authorization endpoint uses it so an
    OpenWebUI user authenticates with Microsoft alone — no AI-Employee login form.

    ``client=ios`` marks a login started from the native app — see
    SSOService.generate_login_url for why that changes how the callback ends.
    """
    from app.core.sso_providers import get_sso_provider, is_sso_available
    from app.services.sso_service import SSOService

    try:
        sso_provider = get_sso_provider(provider)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown SSO provider: {provider}")

    if not is_sso_available(sso_provider):
        raise HTTPException(status_code=400, detail=f"SSO not configured for {provider}")

    redis = request.app.state.redis
    sso_service = SSOService(db, redis)

    try:
        auth_url = await sso_service.generate_login_url(
            provider, return_to=safe_internal_path(redirect),
            request_host=request.headers.get("host"),
            client=client or "",
        )
        return RedirectResponse(url=auth_url, status_code=302)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sso/{provider}/callback")
async def sso_callback(
    provider: str,
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle SSO callback from provider."""
    from app.services.sso_service import SSOService

    # Determine frontend URL for redirects
    frontend_url = settings.oauth_redirect_base_url
    if frontend_url.endswith(":8000"):
        # Dev: orchestrator is on :8000, frontend on :3000
        frontend_url = frontend_url.replace(":8000", ":3000")

    redis = request.app.state.redis
    sso_service = SSOService(db, redis)
    # Peeked up front (before the state is validated/consumed) purely to pick WHERE
    # an error should land — the native app's custom scheme vs. the web login page.
    client = await sso_service.peek_state_client(state)

    def _error_redirect(query: str) -> RedirectResponse:
        base = IOS_SSO_CALLBACK_URL if client == "ios" else f"{frontend_url}/login"
        return RedirectResponse(url=f"{base}?{query}")

    if error:
        return _error_redirect(f"error=sso_{error}&provider={provider}")

    if not code or not state:
        return _error_redirect(f"error=sso_missing_params&provider={provider}")

    try:
        user, return_to = await sso_service.handle_callback(provider, code, state)
    except ValueError as e:
        logger.warning(f"SSO callback failed for {scrub_log(provider)}: {e}")
        await _sso_fehler_protokollieren(db, request, provider, e)
        return _error_redirect(f"error={str(e)}&provider={provider}")

    return await finish_sso_login(user, return_to, provider, frontend_url, client=client, redis=redis, db=db)


#: Single-use exchange code TTL — just long enough for the app to receive the
#: custom-scheme redirect and immediately call /sso/exchange, never longer.
SSO_EXCHANGE_TTL = 60


class SSOExchangeRequest(BaseModel):
    code: str


@router.post("/sso/exchange")
async def sso_exchange(body: SSOExchangeRequest, request: Request):
    """Trade a native SSO callback's one-time code for the actual tokens.

    Public (no session yet — this IS how the session starts), but the code is
    random, single-use (deleted on the first read here, whoever gets there wins),
    and expires within SSO_EXCHANGE_TTL — see finish_sso_login for why the tokens
    never travel in the redirect URL itself.
    """
    redis = request.app.state.redis
    key = f"sso:exchange:{body.code}"
    stored = await redis.client.get(key)
    if not stored:
        raise HTTPException(status_code=400, detail="Der Code ist ungültig oder abgelaufen.")
    await redis.client.delete(key)
    if isinstance(stored, bytes):
        stored = stored.decode()
    return json.loads(stored)


async def finish_sso_login(
    user, return_to: str | None, provider: str, frontend_url: str, client: str = "", redis=None,
    db: AsyncSession | None = None,
):
    """Freigabe pruefen, Ziel bestimmen, Sitzung herstellen.

    Die gemeinsame Endstrecke JEDER Single-Sign-On-Anmeldung — OIDC wie SAML. Wuerde
    SAML das nachbauen, gaebe es zwei Stellen, an denen Sitzungen entstehen: die
    Freigabepflicht koennte an einer davon fehlen, und genau dort kaeme jemand ohne
    Freischaltung herein.

    ``client="ios"`` (nur ueber OIDC erreichbar, SAML kennt kein natives Ziel) traegt
    NICHT die Tokens selbst im Redirect an das Custom-URL-Scheme der App, sondern nur
    einen kurzlebigen Einweg-Austauschcode (siehe /sso/exchange) — anders als eine
    Universal Link ist ein Custom-Scheme nicht exklusiv reserviert, eine andere App
    koennte denselben Scheme-Namen registrieren und den Redirect abfangen. Ein Code,
    der nur einmal und nur fuer Sekunden gegen die eigentlichen Tokens eintauschbar
    ist, begrenzt den Schaden eines solchen Abfangens auf so gut wie nichts.
    """
    # Freigabe steht aus → keine Sitzung, zurueck zur Anmeldung mit Hinweis.
    if not getattr(user, "approved", True):
        logger.info(f"SSO login blocked (pending approval): {user.email}")
        if db is not None:
            await _anmeldung_protokollieren(db, AuditEventType.LOGIN_FAILED, email=user.email,
                                            user=user, weg=f"sso:{provider}",
                                            grund="Freischaltung ausstehend")
        if client == "ios":
            return RedirectResponse(url=f"{IOS_SSO_CALLBACK_URL}?pending=1", status_code=302)
        return RedirectResponse(url=f"{frontend_url}/login?pending=1", status_code=302)

    access = create_access_token(user.id, user.role.value, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    if db is not None:
        await _anmeldung_protokollieren(db, AuditEventType.LOGIN_SUCCEEDED, email=user.email,
                                        user=user, weg=f"sso:{provider}")

    if client == "ios":
        code = secrets.token_urlsafe(32)
        payload = json.dumps({"access_token": access, "refresh_token": refresh})
        await redis.client.setex(f"sso:exchange:{code}", SSO_EXCHANGE_TTL, payload)
        logger.info(f"SSO login successful: {scrub_log(user.email)} via {scrub_log(provider)} (native)")
        return RedirectResponse(url=f"{IOS_SSO_CALLBACK_URL}?code={code}", status_code=302)

    # Where to land: the stored return target (re-validated — it was checked when the
    # login started, and nothing else may reach this), else the dashboard. API paths
    # live on the orchestrator origin, everything else on the frontend (differs in dev).
    target = safe_internal_path(return_to)
    if target:
        base = settings.oauth_redirect_base_url if target.startswith("/api/") else frontend_url
        destination = f"{base.rstrip('/')}{target}"
    else:
        destination = f"{frontend_url}/dashboard"

    # Set auth cookies (same as normal login)
    redirect_resp = RedirectResponse(url=destination, status_code=302)
    redirect_resp.set_cookie(COOKIE_ACCESS, access, max_age=1800, **COOKIE_OPTS)
    redirect_resp.set_cookie(COOKIE_REFRESH, refresh, max_age=604800, **COOKIE_OPTS)

    logger.info(f"SSO login successful: {scrub_log(user.email)} via {scrub_log(provider)}")
    return redirect_resp




# --- Authenticated Endpoints ---


@router.get("/me")
async def get_me(request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    return UserResponse.model_validate(user).model_dump()


@router.post("/me/tutorial-seen")
async def mark_tutorial_seen(request: Request, db: AsyncSession = Depends(get_db)):
    """Willkommensfenster mit den Klick-Tutorials gesehen — es oeffnet sich nicht mehr von selbst.

    Nur der eigene Nutzer; ein zweiter Aufruf aendert den ersten Zeitpunkt nicht.
    """
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    if not user.tutorial_seen_at:
        user.tutorial_seen_at = datetime.now(timezone.utc)
        await db.commit()
    return {"tutorial_seen_at": user.tutorial_seen_at.isoformat()}


@router.get("/me/ui-preferences")
async def get_ui_preferences(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Eigene Oberflaechen-Einstellungen (core/ui_einstellungen) — nichts gespeichert: ``{}``."""
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    return getattr(user, "ui_preferences", None) or {}


@router.patch("/me/ui-preferences")
async def patch_ui_preferences(
    request: Request,
    body: dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Die uebergebenen Schluessel ersetzen, alle anderen bleiben; Antwort: das Ganze.

    Nur das eigene Konto — es gibt keinen Weg, eine fremde Nutzer-ID zu nennen.
    Unbekannter Schluessel, kein Objekt als Wert oder mehr als 32 KB: 422.
    """
    from app.core.ui_einstellungen import UngueltigeEinstellungen, zusammenfuehren
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    if not isinstance(user, User):
        # Einrichtungsmodus (noch kein Konto): es gibt keine Zeile, an der das hinge.
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Zeile sperren: zwei Geraete, die gleichzeitig verschiedene Schluessel
    # schreiben, sollen sich nicht gegenseitig den Stand ueberschreiben.
    user = await db.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    try:
        neu = zusammenfuehren(user.ui_preferences, body)
    except UngueltigeEinstellungen as e:
        raise HTTPException(status_code=422, detail=str(e))
    # Neues Objekt zuweisen — SQLAlchemy erkennt Aenderungen IM JSON-Feld nicht.
    user.ui_preferences = neu
    await db.commit()
    return neu


@router.get("/me/photo")
async def get_me_photo(request: Request, db: AsyncSession = Depends(get_db)):
    """Profile photo of the current user, proxied from Microsoft Graph.

    Uses the per-user Graph token captured during Microsoft SSO login.
    Kein Foto ist kein Fehler: ohne Quelle (oder ohne Bild) kommt 204, die
    Oberflaeche zeigt dann die Initialen (#907). ``/auth/me`` meldet ueber
    ``has_photo_source`` vorab, ob sich die Abfrage lohnt.
    """
    import httpx

    from app.dependencies import get_current_user
    from app.services.oauth_service import OAuthService

    kein_foto = Response(status_code=204)
    user = await get_current_user(request, db)
    if getattr(user, "sso_provider", None) != FOTO_ANBIETER:
        return kein_foto
    try:
        token = await OAuthService(db, None).get_valid_token(FOTO_ANBIETER, user.id)
    except Exception:
        return kein_foto
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/photo/$value",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        return kein_foto
    if resp.status_code != 200 or not resp.content:
        return kein_foto
    # Nur echte Rasterbilder durchreichen: die Inhaltsart kommt von aussen, und
    # ein ``text/html`` oder ``image/svg+xml`` liefe sonst unter der Adresse der
    # Anlage als Seite bzw. Skript. Ohne passende Art: kein Foto.
    art = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if art not in FOTO_INHALTSARTEN:
        return kein_foto
    return Response(
        content=resp.content,
        media_type=art,
        headers={"Cache-Control": "private, max-age=3600",
                 "X-Content-Type-Options": "nosniff"},
    )


# --- Admin-only User Management ---


@router.get("/users")
async def list_users(request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Administratoren.")

    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()

    # Kosten je Nutzer im laufenden Monat — aus der einen Kostenquelle (#896),
    # dieselbe Zahl, mit der das Nutzerbudget geprueft wird.
    from app.core.kosten import Bereich, kosten, monatsbeginn
    from app.models.agent import Agent

    je_agent = (await kosten(db, Bereich.anlage(), seit=monatsbeginn())).je_agent
    besitzer = dict((await db.execute(select(Agent.id, Agent.user_id))).all())
    monthly_cost_by_user: dict[str, float] = defaultdict(float)
    for agent_id, betrag in je_agent.items():
        if besitzer.get(agent_id):
            monthly_cost_by_user[besitzer[agent_id]] += betrag

    users_out = []
    for u in users:
        data = UserResponse.model_validate(u).model_dump()
        data["monthly_cost_usd"] = round(monthly_cost_by_user.get(u.id, 0.0), 4)
        users_out.append(data)
    return {"users": users_out}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, body: UserUpdateRequest, request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Administratoren.")

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")

    alte_rolle = target.role
    geaendert = sorted(k for k, v in body.model_dump(exclude_unset=True).items() if v is not None)
    if body.name is not None:
        target.name = body.name
    if body.allow_personal_credentials is not None:
        target.allow_personal_credentials = body.allow_personal_credentials
    if body.role is not None:
        new_role = UserRole(body.role)
        if target.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
            admin_count = await db.scalar(
                select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
            )
            if admin_count <= 1:
                raise HTTPException(status_code=400, detail="Der letzte Administrator kann nicht entfernt werden.")
        target.role = new_role
    if body.is_active is not None:
        if target.id == current.id:
            raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst deaktivieren.")
        target.is_active = body.is_active
    if body.approved is not None:
        target.approved = body.approved

    if geaendert:
        await _verwaltung(db, AuditEventType.USER_UPDATED, current, target, felder=geaendert)
    if body.role is not None and target.role != alte_rolle:
        await _verwaltung(db, AuditEventType.ROLE_CHANGED, current, target,
                          von=getattr(alte_rolle, "value", str(alte_rolle)), nach=target.role.value)
    await db.commit()
    return UserResponse.model_validate(target).model_dump()


@router.post("/users")
async def create_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Admin-only: Create a new user."""
    from app.dependencies import get_current_user

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Administratoren.")

    body_raw = await request.json()
    name = body_raw.get("name", "").strip()
    email = body_raw.get("email", "").strip()
    password = body_raw.get("password", "")
    role = body_raw.get("role", "member")
    custom_role_id = body_raw.get("custom_role_id")

    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="Name, E-Mail und Passwort werden benötigt.")
    passwort_pruefen(password, email)
    valid_roles = {r.value for r in UserRole}
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(sorted(valid_roles))}")

    # Optional custom role (group) — validate it exists.
    if custom_role_id is not None:
        from app.models.custom_role import CustomRole
        if not await db.get(CustomRole, custom_role_id):
            raise HTTPException(status_code=400, detail="Die gewählte Rolle gibt es nicht.")

    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=409, detail="Diese E-Mail-Adresse ist bereits registriert.")

    user = User(
        id=uuid.uuid4().hex[:12],
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=UserRole(role),
        custom_role_id=custom_role_id,
        approved=True,  # admin-created users are always approved
    )
    db.add(user)
    await _verwaltung(db, AuditEventType.USER_CREATED, current, user, weg="verwaltung",
                      role=user.role.value, custom_role_id=custom_role_id)
    await db.commit()
    await db.refresh(user)

    logger.info(f"Admin {current.email} created user: {user.email} (role: {user.role.value})")
    return UserResponse.model_validate(user).model_dump()


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Admin-only: generate a new random password for a user and return it once."""
    from app.dependencies import get_current_user

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Administratoren.")

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")

    # Dieselben Regeln wie bei Registrierung und Anlage. Ein zufaelliges Passwort
    # erfuellt sie praktisch immer; die Schleife haelt die Zusage auch im Rest.
    from app.core.passwort_regeln import passwort_fehler
    temp_password = secrets.token_urlsafe(12)
    while passwort_fehler(temp_password, target.email):
        temp_password = secrets.token_urlsafe(12)
    target.password_hash = hash_password(temp_password)
    # Revoke every session issued before this reset — otherwise a compromised
    # account stays reachable via its old, still-valid token for up to 7 days.
    target.token_version += 1
    await _verwaltung(db, AuditEventType.PASSWORD_RESET, current, target)
    await db.commit()

    logger.info(f"Admin {current.email} reset password for user: {target.email}")
    return {"user_id": target.id, "email": target.email, "temp_password": temp_password}


@router.post("/users/{user_id}/mfa-reset")
async def reset_user_mfa(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Admin-only: Zwei-Faktor eines Nutzers zurücksetzen (#915) — etwa bei verlorenem Telefon.

    Geheimnis und Wiederherstellungscodes fallen weg, ``token_version`` steigt: alle
    bestehenden Sitzungen UND offene Zwischen-Tokens des Kontos werden ungültig. Ist
    die Pflicht an, richtet der Nutzer bei der nächsten Anmeldung neu ein.
    """
    from app.dependencies import get_current_user

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Administratoren.")

    if user_id == current.id:
        # Den eigenen zweiten Faktor schaltet man in den Einstellungen ab — dort
        # mit Passwort UND Code. Hier ginge es mit der Sitzung allein.
        raise HTTPException(status_code=400,
                            detail="Den eigenen zweiten Faktor schaltest du in den Einstellungen ab.")

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")

    war_aktiv = bool(target.mfa_enabled)
    target.totp_secret_encrypted = None
    target.mfa_enabled_at = None
    target.mfa_recovery_codes = None
    target.token_version += 1
    await _verwaltung(db, AuditEventType.MFA_RESET, current, target, war_aktiv=war_aktiv)
    await db.commit()

    logger.info("Admin %s hat den zweiten Faktor von Nutzer %s zurückgesetzt", scrub_log(current.id), scrub_log(target.id))
    return {"user_id": target.id, "mfa_enabled": False}


#: Was mit den Agenten eines Nutzers passiert, der gelöscht wird (#892).
AGENTEN_BEIM_NUTZERLOESCHEN = ("loeschen", "uebertragen")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    agenten: str | None = Query(None, description="Agenten des Nutzers: 'loeschen' (samt Daten) oder 'uebertragen'"),
    an: str | None = Query(None, description="Ziel beim Übertragen; Standard: der löschende Admin"),
    db: AsyncSession = Depends(get_db),
):
    """Nutzer löschen — mit Rückfrage, wenn er noch Agenten besitzt.

    ``agents.user_id`` verweist ohne ``ON DELETE`` auf den Nutzer: bisher scheiterte
    das Löschen dann mit HTTP 500. Jetzt kommt eine Rückfrage (409) mit der Liste,
    und der Admin entscheidet: Agenten samt Daten löschen oder übertragen.
    """
    from app.dependencies import get_current_user
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess
    from app.models.agent_template import AgentTemplate
    from sqlalchemy import update as sql_update

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Nur für Administratoren.")

    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Du kannst dich nicht selbst löschen.")

    if agenten is not None and agenten not in AGENTEN_BEIM_NUTZERLOESCHEN:
        raise HTTPException(status_code=400, detail="agenten muss 'loeschen' oder 'uebertragen' sein")

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden.")

    eigene = (await db.execute(
        select(Agent).where(Agent.user_id == user_id).order_by(Agent.name)
    )).scalars().all()

    if eigene and agenten is None:
        raise HTTPException(status_code=409, detail={
            "code": "nutzer_hat_agenten",
            "message": (
                f"{target.name} besitzt noch {len(eigene)} Agent(en). Übertrage sie an "
                "eine andere Person oder lösche sie samt Chats, Gedächtnis und Arbeitsordner."
            ),
            "agenten": [{"id": a.id, "name": a.name} for a in eigene],
        })

    if eigene and agenten == "uebertragen":
        ziel_id = an or current.id
        if ziel_id == user_id or not await db.get(User, ziel_id):
            raise HTTPException(status_code=400, detail="Ziel für die Übertragung nicht gefunden")
        for agent in eigene:
            agent.user_id = ziel_id
        await db.flush()
    elif eigene and agenten == "loeschen":
        from app.core.agent_manager import AgentManager

        manager = AgentManager(db, request.app.state.docker, request.app.state.redis)
        for agent in eigene:
            await manager.remove_agent(agent.id, remove_data=True)

    # Weitere Verweise ohne ON DELETE: wer eine Vorlage angelegt oder einen Zugriff
    # gewährt hat. Der Eintrag selbst bleibt, nur der Urheber wird vergessen.
    await db.execute(sql_update(AgentTemplate).where(AgentTemplate.created_by == user_id).values(created_by=None))
    await db.execute(sql_update(AgentAccess).where(AgentAccess.granted_by == user_id).values(granted_by=None))

    await _verwaltung(db, AuditEventType.USER_DELETED, current, target, name=target.name,
                      agenten=agenten if eigene else None, agenten_anzahl=len(eigene))
    await db.delete(target)
    await db.commit()
    logger.info(
        "Admin %s hat Nutzer %s gelöscht (%d Agent(en), %s)",
        scrub_log(current.id), scrub_log(user_id), len(eigene), agenten or "keine",
    )
    return {"ok": True, "agenten": len(eigene), "agenten_aktion": agenten if eigene else None}
