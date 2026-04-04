"""Authentication API endpoints: register, login, logout, user management."""

import logging
import time
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
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
    is_active: bool

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    is_active: bool | None = None


# --- Helpers ---


def _set_auth_cookies(response: Response, user: User) -> dict:
    access = create_access_token(user.id, user.role.value)
    refresh = create_refresh_token(user.id)
    response.set_cookie(COOKIE_ACCESS, access, max_age=1800, **COOKIE_OPTS)
    response.set_cookie(COOKIE_REFRESH, refresh, max_age=604800, **COOKIE_OPTS)
    return {"access_token": access}


# --- Public Endpoints ---


@router.post("/register")
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    # Check if this is the first user (auto-admin)
    count = await db.scalar(select(func.count()).select_from(User))
    is_first = count == 0

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

    user = User(
        id=uuid.uuid4().hex[:12],
        email=body.email,
        name=body.name,
        password_hash=hash_password(body.password),
        role=UserRole.ADMIN if is_first else UserRole.MEMBER,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered: {user.email} (role: {user.role.value}, first: {is_first})")

    tokens = _set_auth_cookies(response, user)
    return {
        "user": UserResponse.model_validate(user).model_dump(),
        **tokens,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    # Brute-force protection: check rate limit per email
    _check_login_rate(body.email)

    user = await db.scalar(select(User).where(User.email == body.email))
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        _record_failed_login(body.email)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

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
async def logout(response: Response):
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
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    tokens = _set_auth_cookies(response, user)
    return {"user": UserResponse.model_validate(user).model_dump(), **tokens}


@router.get("/registration-status")
async def registration_status(db: AsyncSession = Depends(get_db)):
    """Public: check if registration is open and if setup is needed."""
    count = await db.scalar(select(func.count()).select_from(User))
    return {
        "registration_open": settings.registration_open or count == 0,
        "needs_setup": count == 0,
    }


# --- SSO / OIDC Endpoints ---


@router.get("/sso/providers")
async def list_sso_providers():
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
    return {"providers": providers}


@router.get("/sso/{provider}/login")
async def sso_login(
    provider: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Redirect user to SSO provider for authentication."""
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
        auth_url = await sso_service.generate_login_url(provider)
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
        user = await sso_service.handle_callback(provider, code, state)
    except ValueError as e:
        logger.warning(f"SSO callback failed for {provider}: {e}")
        return RedirectResponse(
            url=f"{frontend_url}/login?error={str(e)}&provider={provider}"
        )

    # Set auth cookies (same as normal login)
    redirect_resp = RedirectResponse(url=f"{frontend_url}/dashboard", status_code=302)
    access = create_access_token(user.id, user.role.value)
    refresh = create_refresh_token(user.id)
    redirect_resp.set_cookie(COOKIE_ACCESS, access, max_age=1800, **COOKIE_OPTS)
    redirect_resp.set_cookie(COOKIE_REFRESH, refresh, max_age=604800, **COOKIE_OPTS)

    logger.info(f"SSO login successful: {user.email} via {provider}")
    return redirect_resp


# --- Authenticated Endpoints ---


@router.get("/me")
async def get_me(request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    return UserResponse.model_validate(user).model_dump()


# --- Admin-only User Management ---


@router.get("/users")
async def list_users(request: Request, db: AsyncSession = Depends(get_db)):
    from app.dependencies import get_current_user

    user = await get_current_user(request, db)
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")

    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return {"users": [UserResponse.model_validate(u).model_dump() for u in users]}


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
    if body.role is not None:
        if target.role == UserRole.ADMIN and body.role == "member":
            admin_count = await db.scalar(
                select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
            )
            if admin_count <= 1:
                raise HTTPException(status_code=400, detail="Cannot remove last admin")
        target.role = UserRole(body.role)
    if body.is_active is not None:
        if target.id == current.id:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        target.is_active = body.is_active

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

    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="Name, email, and password are required")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'member'")

    existing = await db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        id=uuid.uuid4().hex[:12],
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=UserRole(role),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"Admin {current.email} created user: {user.email} (role: {user.role.value})")
    return UserResponse.model_validate(user).model_dump()


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
