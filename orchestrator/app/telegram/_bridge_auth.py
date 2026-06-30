"""Authentication helper for the in-process Telegram bridge.

The Telegram bot runs inside the orchestrator container and talks to the
orchestrator HTTP API over localhost. Since v1.84.0 those endpoints require
authentication (the previous ``X-Internal-Secret`` header was removed in the
auth refactor), so the bridge must present a Bearer token like any other API
client.

We mint a short-lived JWT for the platform admin and attach it to the bridge's
HTTP clients. ``require_auth`` resolves it to a real user, so the bot keeps the
internal access it had before without re-introducing a shared static secret.
"""

import httpx
from sqlalchemy import select

from app.core.auth import create_access_token
from app.db.session import async_session_factory
from app.models.user import User, UserRole


async def bridge_token() -> str | None:
    """Mint a short-lived admin JWT for in-process bridge API calls.

    Returns ``None`` if no approved admin exists (caller falls back to an
    unauthenticated client, which will simply get the usual 401).
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.role == UserRole.ADMIN, User.approved == True)  # noqa: E712
            .limit(1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None
        role = user.role.value if hasattr(user.role, "value") else str(user.role)
        return create_access_token(str(user.id), role)


async def authed_client(**kwargs) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` pre-authenticated as the platform admin.

    Use exactly like ``httpx.AsyncClient(...)`` but for calls against the
    orchestrator's own API:

        async with await authed_client() as client:
            resp = await client.get(f"{API_BASE}/agents/")

    Any ``headers`` passed in are preserved and merged with the Bearer token.
    """
    token = await bridge_token()
    headers = dict(kwargs.pop("headers", {}) or {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(headers=headers, **kwargs)
