"""User presence — who is online.

A logged-in client sends a lightweight heartbeat every ~45s; we store a per-user
key in Redis with a short TTL, so "online" = "sent a heartbeat recently". Admins
can list who is currently online. A user can also mark themselves invisible.
"""

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_admin, require_auth
from app.models.user import User
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/presence", tags=["presence"])

_TTL = 90  # seconds — a user is "online" if they heartbeat within this window
_KEY = "presence:online"  # redis hash: user_id -> last_seen_epoch


@router.post("/heartbeat")
async def heartbeat(
    invisible: bool = False,
    user=Depends(require_auth),
    redis: RedisService = Depends(get_redis_service),
):
    """Mark the caller online (or remove them if they chose 'appear offline')."""
    uid = str(getattr(user, "id", ""))
    if not redis.client or not uid:
        return {"ok": True}
    try:
        if invisible:
            await redis.client.hdel(_KEY, uid)
        else:
            await redis.client.hset(_KEY, uid, str(int(time.time())))
    except Exception as e:  # noqa: BLE001
        logger.debug("presence heartbeat failed: %s", e)
    return {"ok": True, "invisible": invisible}


@router.get("/online")
async def online_users(
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """List users currently online (admin only)."""
    if not redis.client:
        return {"online": [], "count": 0}
    now = int(time.time())
    try:
        raw = await redis.client.hgetall(_KEY)
    except Exception:  # noqa: BLE001
        raw = {}
    fresh: dict[str, int] = {}
    stale: list[str] = []
    for uid, seen in (raw or {}).items():
        try:
            last = int(seen)
        except (TypeError, ValueError):
            continue
        if now - last <= _TTL:
            fresh[str(uid)] = last
        else:
            stale.append(str(uid))
    if stale:  # opportunistic cleanup of expired entries
        try:
            await redis.client.hdel(_KEY, *stale)
        except Exception:  # noqa: BLE001
            pass
    users = []
    if fresh:
        rows = (await db.execute(select(User).where(User.id.in_(list(fresh.keys()))))).scalars().all()
        for u in rows:
            users.append({
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "role": u.role.value if hasattr(u.role, "value") else str(u.role),
                "last_seen_seconds_ago": now - fresh.get(u.id, now),
            })
        users.sort(key=lambda x: x["name"].lower())
    return {"online": users, "count": len(users)}
