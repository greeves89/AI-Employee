import hashlib
import hmac
import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


def get_redis_service(request: Request) -> RedisService:
    return request.app.state.redis


def get_docker_service(request: Request) -> DockerService:
    return request.app.state.docker


# --- User Authentication (JWT from httpOnly cookie) ---


async def _check_users_exist(db: AsyncSession) -> bool:
    """Check if any users have been registered yet."""
    from sqlalchemy import func
    try:
        from app.models.user import User
        count = await db.scalar(select(func.count()).select_from(User))
        return count > 0
    except Exception:
        # Table doesn't exist yet (no migration) - no users
        return False


class _AnonymousUser:
    """Placeholder user when no users exist yet (setup mode)."""
    id = "__anonymous__"
    email = "anonymous@setup"
    name = "Anonymous"
    role = None
    is_active = True

    def __init__(self):
        from app.models.user import UserRole
        self.role = UserRole.ADMIN  # Grant admin during setup


async def get_current_user(request: Request, db: AsyncSession) -> "User":
    """Extract and validate JWT from access_token cookie. Returns User or raises 401.

    If no users have registered yet (setup mode), returns an anonymous admin
    to allow the platform to function before first registration.
    """
    from app.core.auth import decode_token
    from app.models.user import User

    token = request.cookies.get("access_token")

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
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


async def require_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """FastAPI Depends() wrapper: returns authenticated User or raises 401."""
    return await get_current_user(request, db)


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
    if not user or not user.is_active:
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

    # Also try to extract agent_id from request body for POST requests
    if not agent_id:
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
