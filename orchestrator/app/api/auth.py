"""Authentication API endpoints: register, login, logout, user management."""

import logging
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
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
from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# --- Login brute-force protection ---
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SECONDS = 300  # 5 minutes
_login_attempts: dict[str, list[float]] = defaultdict(list)


def _check_login_rate(email: str) -> None:
    """Block login if too many failed attempts for this email."""
    now = time.time()
    attempts = _login_attempts[email]
    # Clean old entries
    _login_attempts[email] = [t for t in attempts if now - t < _LOGIN_WINDOW_SECONDS]
    if len(_login_attempts[email]) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Try again in {_LOGIN_WINDOW_SECONDS // 60} minutes.",
        )


def _record_failed_login(email: str) -> None:
    _login_attempts[email].append(time.time())


def _clear_login_attempts(email: str) -> None:
    _login_attempts.pop(email, None)

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

    model_config = {"from_attributes": True}


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
                detail="Setup token required for first admin registration. "
                "Provide the SETUP_TOKEN from your .env file.",
            )

    # If not first user, check if registration is open
    if not is_first and not settings.registration_open:
        raise HTTPException(status_code=403, detail="Registration is closed")

    # Check duplicate email
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    # Validate password
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

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
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered: {user.email} (role: {user.role.value}, first: {is_first}, approved: {approved})")

    # Pending approval → no session; the frontend shows a "wait for admin" notice.
    if not approved:
        return {"pending": True, "user": UserResponse.model_validate(user).model_dump()}

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
        raise HTTPException(status_code=403, detail="Password login is disabled — please sign in with Microsoft.")

    # Brute-force protection: check rate limit per email
    _check_login_rate(body.email)

    user = await db.scalar(select(User).where(User.email == body.email))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        _record_failed_login(body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    if not getattr(user, "approved", True):
        raise HTTPException(status_code=403, detail="Dein Konto wartet noch auf Freischaltung durch einen Administrator.")

    _clear_login_attempts(body.email)
    tokens = _set_auth_cookies(response, user)

    # Update activity + wake user's agents (fire-and-forget)
    from datetime import datetime, timezone
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
    response.delete_cookie(COOKIE_ACCESS, path="/")
    response.delete_cookie(COOKIE_REFRESH, path="/")
    return {"ok": True}


@router.post("/refresh")
async def refresh_token(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Refresh access token using refresh cookie."""
    token = request.cookies.get(COOKIE_REFRESH)
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
        return RedirectResponse(url=f"{frontend_url}/login?error={e}")

    groups = saml_config.extract_groups(attributes, cfg)
    await sso_service.apply_group_role(user, saml_config.PROVIDER_NAME, groups)

    relay = form.get("RelayState") or ""
    return finish_sso_login(user, relay, saml_config.PROVIDER_NAME, frontend_url)


@router.get("/sso/{provider}/login")
async def sso_login(
    provider: str,
    request: Request,
    redirect: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Redirect user to SSO provider for authentication.

    ``redirect`` is an optional internal path to land on after a successful login
    (instead of the dashboard). The MS-Graph MCP authorization endpoint uses it so an
    OpenWebUI user authenticates with Microsoft alone — no AI-Employee login form.
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
            provider, return_to=safe_internal_path(redirect)
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

    if error:
        return RedirectResponse(
            url=f"{frontend_url}/login?error=sso_{error}&provider={provider}"
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_url}/login?error=sso_missing_params&provider={provider}"
        )

    redis = request.app.state.redis
    sso_service = SSOService(db, redis)

    try:
        user, return_to = await sso_service.handle_callback(provider, code, state)
    except ValueError as e:
        logger.warning(f"SSO callback failed for {scrub_log(provider)}: {e}")
        return RedirectResponse(
            url=f"{frontend_url}/login?error={str(e)}&provider={provider}"
        )

    return finish_sso_login(user, return_to, provider, frontend_url)


def finish_sso_login(user, return_to: str | None, provider: str, frontend_url: str):
    """Freigabe pruefen, Ziel bestimmen, Sitzungs-Cookies setzen.

    Die gemeinsame Endstrecke JEDER Single-Sign-On-Anmeldung — OIDC wie SAML. Wuerde
    SAML das nachbauen, gaebe es zwei Stellen, an denen Sitzungen entstehen: die
    Freigabepflicht koennte an einer davon fehlen, und genau dort kaeme jemand ohne
    Freischaltung herein.
    """
    # Freigabe steht aus → keine Sitzung, zurueck zur Anmeldung mit Hinweis.
    if not getattr(user, "approved", True):
        logger.info(f"SSO login blocked (pending approval): {user.email}")
        return RedirectResponse(url=f"{frontend_url}/login?pending=1", status_code=302)

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
    access = create_access_token(user.id, user.role.value, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
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


@router.get("/me/photo")
async def get_me_photo(request: Request, db: AsyncSession = Depends(get_db)):
    """Profile photo of the current user, proxied from Microsoft Graph.

    Uses the per-user Graph token captured during Microsoft SSO login.
    404 when there is no photo source — the frontend falls back to initials.
    """
    import httpx

    from app.dependencies import get_current_user
    from app.services.oauth_service import OAuthService

    user = await get_current_user(request, db)
    if user.sso_provider != "microsoft":
        raise HTTPException(status_code=404, detail="No photo source")
    try:
        token = await OAuthService(db, None).get_valid_token("microsoft", user.id)
    except Exception:
        raise HTTPException(status_code=404, detail="No photo source")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/photo/$value",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        raise HTTPException(status_code=404, detail="No photo")
    if resp.status_code != 200 or not resp.content:
        raise HTTPException(status_code=404, detail="No photo")
    return Response(
        content=resp.content,
        media_type=resp.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --- Admin-only User Management ---


@router.get("/users")
async def list_users(request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()

    from app.models.agent import Agent
    from app.models.task import Task
    from app.models.chat_message import ChatMessage

    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    task_cost_rows = await db.execute(
        select(Agent.user_id, func.coalesce(func.sum(Task.cost_usd), 0))
        .join(Task, Task.agent_id == Agent.id)
        .where(Task.cost_usd.isnot(None), Task.created_at >= month_start)
        .group_by(Agent.user_id)
    )
    chat_cost_rows = await db.execute(
        select(Agent.user_id, func.coalesce(func.sum(ChatMessage.cost_usd), 0))
        .join(ChatMessage, ChatMessage.agent_id == Agent.id)
        .where(ChatMessage.cost_usd.isnot(None), ChatMessage.timestamp >= month_start)
        .group_by(Agent.user_id)
    )
    monthly_cost_by_user: dict[str, float] = defaultdict(float)
    for user_id, cost in [*task_cost_rows.all(), *chat_cost_rows.all()]:
        if user_id:
            monthly_cost_by_user[user_id] += float(cost or 0)

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
        raise HTTPException(status_code=403, detail="Admin only")

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

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
                raise HTTPException(status_code=400, detail="Cannot remove last admin")
        target.role = new_role
    if body.is_active is not None:
        if target.id == current.id:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        target.is_active = body.is_active
    if body.approved is not None:
        target.approved = body.approved

    await db.commit()
    return UserResponse.model_validate(target).model_dump()


@router.post("/users")
async def create_user(request: Request, db: AsyncSession = Depends(get_db)):
    """Admin-only: Create a new user."""
    from app.dependencies import get_current_user

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")

    body_raw = await request.json()
    name = body_raw.get("name", "").strip()
    email = body_raw.get("email", "").strip()
    password = body_raw.get("password", "")
    role = body_raw.get("role", "member")
    custom_role_id = body_raw.get("custom_role_id")

    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="Name, email, and password are required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    valid_roles = {r.value for r in UserRole}
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Role must be one of: {', '.join(sorted(valid_roles))}")

    # Optional custom role (group) — validate it exists.
    if custom_role_id is not None:
        from app.models.custom_role import CustomRole
        if not await db.get(CustomRole, custom_role_id):
            raise HTTPException(status_code=400, detail="custom_role_id not found")

    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

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
        raise HTTPException(status_code=403, detail="Admin only")

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    temp_password = secrets.token_urlsafe(12)
    target.password_hash = hash_password(temp_password)
    # Revoke every session issued before this reset — otherwise a compromised
    # account stays reachable via its old, still-valid token for up to 7 days.
    target.token_version += 1
    await db.commit()

    logger.info(f"Admin {current.email} reset password for user: {target.email}")
    return {"user_id": target.id, "email": target.email, "temp_password": temp_password}


@router.delete("/users/{user_id}")
async def delete_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    current = await get_current_user(request, db)
    if current.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")

    if user_id == current.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    target = await db.scalar(select(User).where(User.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    await db.delete(target)
    await db.commit()
    return {"ok": True}
