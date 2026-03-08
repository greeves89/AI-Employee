"""Notification API - agents send notifications, UI reads/marks them."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth, verify_agent_token
from app.models.notification import Notification
from app.services.redis_service import RedisService

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationCreate(BaseModel):
    agent_id: str
    type: str = "info"  # info, warning, error, success, approval
    title: str
    message: str = ""
    priority: str = "normal"  # low, normal, high, urgent
    action_url: str | None = None
    meta: dict | None = None  # for approval: {"options": ["Yes", "No"], "approval_id": "..."}


# --- Agent-facing: create notifications ---

@router.post("/", status_code=201)
async def create_notification(
    body: NotificationCreate,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
    _auth: dict = Depends(verify_agent_token),
):
    """Create a notification (called by agents via internal API, requires auth)."""
    notif = Notification(
        agent_id=body.agent_id,
        type=body.type,
        title=body.title,
        message=body.message,
        priority=body.priority,
        action_url=body.action_url,
        meta=body.meta,
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    # Push to WebSocket clients via Redis PubSub
    event = json.dumps({
        "type": "notification",
        "data": _to_response(notif),
    })
    await redis.client.publish("notifications:live", event)

    # Send Telegram for high/urgent priority
    if body.priority in ("high", "urgent"):
        await _send_telegram(body, redis)

    return _to_response(notif)


# --- UI-facing: list, count, mark read ---

@router.get("/")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the UI notification center."""
    query = select(Notification)
    if unread_only:
        query = query.where(Notification.read == False)  # noqa: E712
    query = query.order_by(Notification.created_at.desc()).limit(limit)
    result = await db.execute(query)
    notifications = result.scalars().all()
    return {"notifications": [_to_response(n) for n in notifications]}


@router.get("/count")
async def unread_count(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Get unread notification count (for badge)."""
    result = await db.execute(
        select(func.count(Notification.id)).where(Notification.read == False)  # noqa: E712
    )
    count = result.scalar() or 0
    return {"unread": count}


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Mark a notification as read."""
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.read = True
    await db.commit()
    return {"status": "read"}


@router.post("/read-all")
async def mark_all_read(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Mark all notifications as read."""
    await db.execute(
        update(Notification)
        .where(Notification.read == False)  # noqa: E712
        .values(read=True)
    )
    await db.commit()
    return {"status": "all_read"}


@router.delete("/{notification_id}")
async def delete_notification(notification_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Delete a notification."""
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(notif)
    await db.commit()
    return {"deleted": notification_id}


async def _send_telegram(body: NotificationCreate, redis: RedisService) -> None:
    """Send high-priority notifications via Telegram.

    Routes to:
    1. Per-agent Telegram bot (if configured) -> all authorized users
    2. Global Telegram bot (fallback) -> admin chat
    """
    # 1. Try per-agent bot first
    try:
        import app.main as main_mod
        tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
        if tg_manager and body.agent_id:
            bot = tg_manager.get_bot(body.agent_id)
            if bot and bot._started:
                emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅"}.get(body.type, "📢")
                text = f"{emoji} *{body.title}*"
                if body.message:
                    text += f"\n\n{body.message}"
                await bot.send_to_all_authorized(text)
                return  # Sent via agent bot, no need for global bot
    except Exception:
        pass

    # 2. Fallback: global bot
    try:
        from app.config import settings
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return
        await redis.client.publish("telegram:notifications", json.dumps({
            "title": body.title,
            "message": body.message,
            "priority": body.priority,
            "agent_id": body.agent_id,
        }))
    except Exception:
        pass


def _to_response(n: Notification) -> dict:
    return {
        "id": n.id,
        "agent_id": n.agent_id,
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "priority": n.priority,
        "read": n.read,
        "action_url": n.action_url,
        "meta": n.meta,
        "created_at": n.created_at.isoformat() if n.created_at else "",
    }
