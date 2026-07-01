"""Authentication helper for the in-process Telegram bridge.

The Telegram bot runs inside the orchestrator container and talks to the
orchestrator HTTP API over localhost. Since v1.84.0 those endpoints require
authentication (the previous ``X-Internal-Secret`` header was removed in the
auth refactor), so the bridge must present a Bearer token like any other API
client.

We mint a short-lived JWT for the platform admin and attach it to the bridge's
HTTP clients. ``require_auth`` resolves it to a real user, so the bot keeps the
internal access it had before without re-introducing a shared static secret.

Token caching: JWTs are valid for 30 minutes (per create_access_token default).
We cache the token for 25 minutes so at most one DB round-trip occurs per
refresh cycle regardless of how many concurrent bot commands fire.
"""

import asyncio
import time

import httpx
from sqlalchemy import select

from app.core.auth import create_access_token
from app.db.session import async_session_factory
from app.models.user import User, UserRole

_TOKEN_CACHE: str | None = None
_TOKEN_EXPIRY: float = 0.0
_TOKEN_TTL_SECONDS = 25 * 60  # 25 min — JWT valid 30 min, 5 min safety margin
_cache_lock = asyncio.Lock()


async def bridge_token() -> str:
    """Mint (or return cached) short-lived admin JWT for bridge API calls.

    Raises RuntimeError if no approved admin exists so callers surface a clear
    error instead of silently sending unauthenticated requests that produce the
    same 401/KeyError the PR was meant to fix.
    """
    global _TOKEN_CACHE, _TOKEN_EXPIRY

    now = time.monotonic()
    if _TOKEN_CACHE and now < _TOKEN_EXPIRY:
        return _TOKEN_CACHE

    async with _cache_lock:
        # Re-check inside the lock to avoid a thundering herd on expiry
        now = time.monotonic()
        if _TOKEN_CACHE and now < _TOKEN_EXPIRY:
            return _TOKEN_CACHE

        async with async_session_factory() as session:
            result = await session.execute(
                select(User)
                .where(User.role == UserRole.ADMIN, User.approved == True)  # noqa: E712
                .order_by(User.created_at)
                .limit(1)
            )
            user = result.scalar_one_or_none()

        if user is None:
            raise RuntimeError(
                "No approved admin user found — cannot mint bridge token. "
                "Ensure at least one admin account is approved in the platform."
            )

        token = create_access_token(str(user.id), user.role.value)
        _TOKEN_CACHE = token
        _TOKEN_EXPIRY = time.monotonic() + _TOKEN_TTL_SECONDS
        return token


async def authed_client(**kwargs) -> httpx.AsyncClient:
    """Return an ``httpx.AsyncClient`` pre-authenticated as the platform admin.

    Use exactly like ``httpx.AsyncClient(...)`` but for calls against the
    orchestrator's own API:

        async with await authed_client() as client:
            resp = await client.get(f"{API_BASE}/agents/")

    Any ``headers`` passed in are preserved and merged with the Bearer token.
    Raises RuntimeError (propagated from bridge_token) if no admin exists.
    """
    token = await bridge_token()
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(headers=headers, **kwargs)
