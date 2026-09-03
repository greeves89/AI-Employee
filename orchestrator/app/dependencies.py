import hashlib
import hmac
import logging
import re
from types import SimpleNamespace

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # nur fuer Typpruefer — zur Laufzeit gaebe es einen Importzyklus
    from app.models.user import User

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


class AgentPrincipal(SimpleNamespace):
    """Authenticated agent caller.

    Kept attribute-compatible with earlier pseudo-users (`id`, `role`) while
    adding an explicit marker so endpoints do not confuse agents with users.
    """

    principal_type = "agent"
    role = "agent"


def is_agent_principal(principal) -> bool:
    """Return True when the authenticated caller is an agent token."""
    return getattr(principal, "principal_type", None) == "agent" or getattr(principal, "role", None) == "agent"


def get_redis_service(request: Request) -> RedisService:
    return request.app.state.redis


def get_docker_service(request: Request) -> DockerService:
    return request.app.state.docker


# --- User Authentication (JWT from httpOnly cookie) ---


async def _check_users_exist(db: AsyncSession) -> bool:
    """Check if any users have been registered yet.

    Fail-closed: only a genuinely missing users table (fresh install, no
    migration yet) counts as "no users -> setup mode". Any other DB error
    (connection lost, timeout, permission) propagates so the caller denies
    the request instead of granting an anonymous ADMIN with RLS bypass.
    """
    from sqlalchemy import func
    from sqlalchemy.exc import ProgrammingError
    from app.models.user import User
    try:
        count = await db.scalar(select(func.count()).select_from(User))
        return (count or 0) > 0
    except ProgrammingError:
        # users table does not exist yet (no migration) - genuine setup mode
        return False


class _AnonymousUser:
    """Placeholder user when no users exist yet (setup mode)."""
    id = "__anonymous__"
    email = "anonymous@setup"
    name = "Anonymous"
    role = None
    is_active = True
    approved = True  # setup-mode placeholder is always usable

    def __init__(self):
        from app.models.user import UserRole
        self.role = UserRole.ADMIN  # Grant admin during setup


# Was ein Konto OHNE zugewiesene Rolle noch darf. Bewusst winzig: gerade so viel,
# dass die Oberflaeche erfahren kann, warum sie leer bleibt, und dass man sich wieder
# abmelden kann. Alles andere ist zu.
_ALLOWED_WHILE_UNASSIGNED = re.compile(
    r"^/api/v1/(auth/(me|logout|refresh|providers)|version|health)(/.*)?$"
)


def _is_unassigned(user) -> bool:
    """Angemeldet, aber noch nichts zugeteilt.

    Ueber den Textwert verglichen, nicht ueber das Enum: derselbe Nutzer kommt an
    manchen Stellen als ORM-Objekt und an anderen als schlichtes Abbild vorbei, und
    ein Vergleich, der davon abhaengt, waere genau die Art Sperre, die im falschen
    Moment durchlaesst.
    """
    role = getattr(user, "role", None)
    return str(getattr(role, "value", role) or "") == "unassigned"


async def get_current_user(request: Request, db: AsyncSession) -> "User":
    """Extract and validate JWT from access_token cookie. Returns User or raises 401.

    If no users have registered yet (setup mode), returns an anonymous admin
    to allow the platform to function before first registration.
    """
    from app.core.auth import decode_token
    from app.models.user import User

    token = request.cookies.get("access_token")

    # Also accept Bearer token in Authorization header (for API clients / bridge)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()

    # Setup mode: no users registered yet -> allow anonymous access
    if not token:
        if not await _check_users_exist(db):
            return _AnonymousUser()
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")

    user = await db.scalar(select(User).where(User.id == payload["sub"]))
    if not user or not user.is_active or not getattr(user, "approved", True):
        raise HTTPException(status_code=401, detail="User not found, inactive, or pending approval")
    # Admin password reset bumps token_version — tokens minted before the reset
    # must stop working immediately, not just wait out their own expiry.
    if payload.get("tv", 0) != user.token_version:
        raise HTTPException(status_code=401, detail="Session revoked")

    # Ohne zugewiesene Rolle bleibt die Plattform zu (#560-Folge, Kundenmeldung).
    #
    # Hier und nur hier, weil hier JEDE Anfrage der Oberflaeche vorbeikommt. Die
    # Menuepunkte zu verstecken waere keine Sperre — ``menu_paths`` liest nur die
    # Seitenleiste, wer die Adresse tippt, waere drin.
    #
    # Der MCP-Weg fuehrt bewusst NICHT hier vorbei: ``/oauth/authorize`` liest das
    # Cookie direkt, und die MCP-Aufrufe selbst tragen ein Bearer-Token. Genau das
    # ist der Zweck — Postfach ja, Plattform nein.
    if _is_unassigned(user) and not _ALLOWED_WHILE_UNASSIGNED.match(request.url.path):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "role_unassigned",
                "message": (
                    "Dein Konto ist angelegt, aber noch keiner Rolle zugeordnet. "
                    "Bitte wende dich an einen Administrator."
                ),
            },
        )

    # Row-Level Security: restrict this session to rows owned by this user.
    # Admins bypass RLS so they can manage all tenants.
    from app.db.session import set_rls_user
    from app.models.user import UserRole
    if user.role == UserRole.ADMIN:
        await set_rls_user(db, None)  # bypass RLS
    else:
        await set_rls_user(db, user.id)

    # Update activity timestamp (for lifecycle manager) — throttle to once per minute.
    # IMPORTANT: we must NOT call db.commit() on the request session because
    # it ends the transaction and destroys SET LOCAL RLS settings, causing
    # all subsequent queries in the endpoint to return empty results.
    # Use a separate short-lived session instead.
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    if not user.last_active_at or user.last_active_at < now - timedelta(minutes=1):
        from app.db.session import async_session_factory
        async with async_session_factory() as activity_session:
            await activity_session.execute(
                sa_text("UPDATE users SET last_active_at = NOW() WHERE id = :uid"),
                {"uid": str(user.id)},
            )
            await activity_session.commit()
        # Update the in-memory object so we don't re-trigger within the same minute
        user.last_active_at = now

    return user


async def require_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """FastAPI Depends() wrapper: returns authenticated User or raises 401."""
    return await get_current_user(request, db)


async def optional_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Like ``require_auth``, but returns ``None`` instead of raising on 401.

    ONLY for routes that have their own authorisation gate and may legitimately
    serve an anonymous caller — currently just the app proxy, where a public
    share token can stand in for a login (#467). Every such route must decide
    for itself what an anonymous caller may see; ``None`` is never "allowed".

    The setup-mode placeholder counts as anonymous here, NOT as the admin it
    pretends to be for the rest of the platform: before the first registration
    ``get_current_user`` hands out an admin for a request with no token at all,
    and these routes are the ones strangers can reach on purpose. Bootstrapping
    the platform must not double as a way into someone's apps.
    """
    try:
        user = await get_current_user(request, db)
    except HTTPException:
        return None
    return None if isinstance(user, _AnonymousUser) else user


async def require_admin(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """FastAPI Depends() wrapper: returns authenticated admin User or raises 403."""
    user = await get_current_user(request, db)
    from app.models.user import UserRole

    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


async def require_manager(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """FastAPI Depends(): returns user with admin or manager role, or raises 403."""
    user = await get_current_user(request, db)
    from app.models.user import UserRole

    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        raise HTTPException(status_code=403, detail="Manager or admin access required")
    return user


async def require_agent_access(
    agent_id: str,
    user,
    db: AsyncSession,
) -> None:
    """Verify user has access to a specific agent.

    - Admin/Manager: always allowed
    - Member/Viewer: only if they own the agent or have AgentAccess entry
    """
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess
    from app.models.user import UserRole

    if user.role in (UserRole.ADMIN, UserRole.MANAGER):
        return

    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Owner check
    if agent.user_id and agent.user_id == user.id:
        return

    # AgentAccess check
    access = await db.scalar(
        select(AgentAccess).where(
            AgentAccess.agent_id == agent_id,
            AgentAccess.user_id == user.id,
        )
    )
    if access:
        return

    raise HTTPException(status_code=403, detail="Access denied to this agent")


async def get_current_user_ws(token: str | None, db: AsyncSession) -> "User":
    """Validate JWT token for WebSocket connections (token from query param).

    If no users exist yet (setup mode), returns anonymous admin.
    """
    from app.core.auth import decode_token
    from app.models.user import User

    if not token:
        # Setup mode: no users -> allow anonymous WS
        if not await _check_users_exist(db):
            return _AnonymousUser()
        return None

    try:
        payload = decode_token(token)
    except Exception:
        return None

    if payload.get("type") != "access":
        return None

    user = await db.scalar(select(User).where(User.id == payload["sub"]))
    if not user or not user.is_active or not getattr(user, "approved", True):
        return None
    if payload.get("tv", 0) != user.token_version:
        return None

    return user


# --- Agent Token Authentication (for agent-to-orchestrator communication) ---


def make_agent_token(agent_id: str) -> str:
    """Derive a deterministic token for an agent using HMAC-SHA256."""
    return hmac.new(
        settings.api_secret_key.encode(),
        agent_id.encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


async def verify_agent_token(request: Request) -> dict:
    """Verify that the request comes from an authenticated agent.

    Checks Authorization header against HMAC-derived token.
    Returns {"agent_id": str} on success.
    """
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    agent_id = request.headers.get("X-Agent-ID", "")

    # Also try to extract agent_id from request body for POST requests only
    if not agent_id and request.method == "POST":
        try:
            body = await request.json()
            agent_id = body.get("agent_id", "")
        except Exception:
            pass

    if not agent_id or not token:
        raise HTTPException(status_code=401, detail="Missing agent credentials")

    expected = make_agent_token(agent_id)
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Invalid agent token")

    return {"agent_id": agent_id}


# --- Sentinel Credential (exclusive to the in-process Sentinel supervisor, #590) ---


_SENTINEL_TOKEN_DOMAIN = b"sentinel:internal-credential:v1"


def get_sentinel_token() -> str:
    """Derive the Sentinel-exclusive credential deterministically from api_secret_key.

    Same domain-separated-HMAC pattern already used for make_agent_token() above,
    the per-agent Redis ACL password (redis_service.py) and the MCP OAuth client
    secret (mcp_oauth.py): no extra secret to generate, store in the DB, or rotate
    independently of api_secret_key.

    Unlike make_agent_token(), the domain string here is a *fixed constant*, not an
    agent_id — this is intentionally one single, non-agent-scoped credential, because
    it identifies the Sentinel *process* (there is exactly one, per #590), not any
    individual agent.

    Why this is safe to derive from the same master secret agent tokens also come
    from: agent containers only ever receive their own per-agent AGENT_TOKEN (a
    *different* HMAC domain, keyed by agent_id) — never api_secret_key itself
    (verified: no API_SECRET_KEY/api_secret_key entry appears anywhere in the
    environment dict AgentManager builds for a container). HMAC is a PRF: knowing
    any number of (agent_id, make_agent_token(agent_id)) pairs does not let a
    compromised agent recover api_secret_key or forge a value for a *different*
    domain string such as this one. That is the concrete guarantee behind #590's
    requirement that "das Credential verlässt den Orchestrator-Prozess nie und
    gelangt nie in einen Agent-Container" — it is computed fresh on every check,
    never persisted anywhere, and structurally unreachable from agent code.
    """
    return hmac.new(
        settings.api_secret_key.encode(),
        _SENTINEL_TOKEN_DOMAIN,
        hashlib.sha256,
    ).hexdigest()


class SentinelPrincipal(SimpleNamespace):
    """Authenticated Sentinel-process caller — see require_sentinel().

    Kept attribute-compatible with AgentPrincipal/User (a `role` attribute) so
    call sites that only branch on ``role``/``principal_type`` do not need a
    third special case, while still being unambiguously distinguishable via
    ``is_sentinel_principal()``.
    """

    principal_type = "sentinel"
    role = "sentinel"


def is_sentinel_principal(principal) -> bool:
    """Return True when the authenticated caller is the Sentinel process."""
    return getattr(principal, "principal_type", None) == "sentinel"


async def require_sentinel(request: Request) -> SentinelPrincipal:
    """FastAPI Depends() wrapper: third credential scheme, exclusive to the Sentinel process.

    Neither ``require_auth``/``require_admin`` (human JWT — Sentinel is not a logged-in
    user) nor ``verify_agent_token`` (per-agent HMAC — the very actor Sentinel may need
    to act against, and therefore must never be able to impersonate it) fit here. See
    #588's manipulation-proof analysis and #590 scope point 3.

    Checks the Authorization header against ``get_sentinel_token()`` with a
    constant-time comparison (``hmac.compare_digest``), same as ``verify_agent_token``,
    to avoid a timing side-channel. Endpoints that accept this alongside
    ``require_auth`` should use an explicit combinator (see ``require_auth_or_agent``
    for the analogous pattern) rather than silently widening an existing
    human-only dependency — left to the call site that actually wires Sentinel's
    privileged action to a route (#590 scope point 4), so the accepted-caller set of
    any given endpoint stays an explicit, reviewable decision.
    """
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, get_sentinel_token()):
        raise HTTPException(status_code=403, detail="Sentinel credential required")
    return SentinelPrincipal()


async def require_auth_or_agent(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Accept either a User JWT or an Agent HMAC token.

    Returns a User object or a pseudo-user SimpleNamespace for agents.
    """
    # Try user JWT first
    try:
        return await get_current_user(request, db)
    except HTTPException:
        pass

    # Fall back to agent token
    try:
        agent_info = await verify_agent_token(request)
        return AgentPrincipal(
            id=agent_info["agent_id"],
            username=f"agent-{agent_info['agent_id']}",
        )
    except HTTPException:
        raise HTTPException(status_code=401, detail="Authentication required (user or agent token)")
