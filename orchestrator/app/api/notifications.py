"""Notification API - agents send notifications, UI reads/marks them."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log
from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth, verify_agent_token
from app.models.notification import Notification
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# "Meldebremse" (PROACTIVE_PROMPT STEP 3): a proactive agent that runs out of planned
# work may check in with the user at most once per half-day. The prompt asks agents to
# self-throttle, but nine idle agents all messaging at once is exactly the failure mode
# this exists to stop — so it's also enforced here as a backstop, keyed per agent.
CHECKIN_COOLDOWN_SECONDS = 12 * 60 * 60


async def _checkin_allowed(redis: RedisService, agent_id: str) -> bool:
    """True if this agent may send another is_checkin notification right now.

    Uses SET NX EX so only the first call within the window succeeds — fails open
    (allows the notification) if Redis is unreachable, since a missed cooldown is
    far cheaper than silently dropping a real user-facing notification.
    """
    if not redis.client:
        return True
    try:
        acquired = await redis.client.set(
            f"notify:checkin_cooldown:{agent_id}", "1", nx=True, ex=CHECKIN_COOLDOWN_SECONDS
        )
        return bool(acquired)
    except Exception:
        return True


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
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
    _auth: dict = Depends(verify_agent_token),
):
    """Create a notification (called by agents via internal API, requires auth)."""
    # The authenticated caller's own id is authoritative, never the client-supplied
    # body.agent_id — verify_agent_token only checks the token against WHATEVER
    # agent_id it was given (header first, else this same body field), so an agent
    # sending its own valid token alongside a different agent_id in the body would
    # otherwise create/route notifications (and, with is_checkin, poison another
    # agent's check-in cooldown) as that other agent. Every real caller already
    # only ever sends its own id here, so overriding it is a no-op for legitimate
    # use and closes the spoofing path.
    body.agent_id = _auth["agent_id"]

    is_checkin = bool((body.meta or {}).get("is_checkin"))
    if is_checkin and body.priority not in ("high", "urgent"):
        if not await _checkin_allowed(redis, body.agent_id):
            logger.info("Check-in notification suppressed by cooldown for agent=%s", scrub_log(body.agent_id))
            response.status_code = 200  # nothing was created — 201 would be misleading
            return {"suppressed": True, "reason": "checkin_cooldown", "agent_id": body.agent_id}

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

    target_channel = (body.meta or {}).get("target_channel", "webapp")

    # Send Telegram when explicitly targeted, or for high/urgent fallback.
    if target_channel in ("telegram", "all") or body.priority in ("high", "urgent"):
        await _send_telegram(body, redis, notif_id=notif.id)

    # Push an die Geraete des Besitzers — iOS UND Browser, beides ueber denselben
    # Verteilpunkt. "ios" bleibt als Kanalname zulaessig, weil bestehende Aufrufer
    # ihn schicken; er heisst faktisch "an seine Geraete".
    if target_channel in ("ios", "web", "push", "all"):
        try:
            from app.models.agent import Agent
            from app.core.push import push_to_user

            agent = (await db.execute(
                select(Agent).where(Agent.id == body.agent_id)
            )).scalar_one_or_none()
            if agent and agent.user_id:
                await push_to_user(
                    db,
                    agent.user_id,
                    body.title,
                    body.message or body.title,
                    data=_push_payload(notif),
                )
        except Exception:  # noqa: BLE001
            logger.exception("Geraete-Push fuer Meldung fehlgeschlagen")

    return _to_response(notif)


class DeviceRegister(BaseModel):
    token: str
    platform: str = "ios"


@router.post("/register-device")
async def register_device(
    body: DeviceRegister,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Register (or refresh) an APNs device token for the current user."""
    from app.models.device_token import DeviceToken

    existing = (await db.execute(
        select(DeviceToken).where(DeviceToken.token == body.token)
    )).scalar_one_or_none()
    if existing:
        existing.user_id = str(user.id)
        existing.platform = body.platform
    else:
        db.add(DeviceToken(
            user_id=str(user.id), token=body.token, platform=body.platform
        ))
    await db.commit()
    return {"status": "registered"}


@router.delete("/register-device")
async def unregister_device(
    body: DeviceRegister,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Geraet abmelden — beim Abmelden aus der App aufzurufen.

    Ohne diesen Schritt bliebe der Geraete-Schluessel beim abgemeldeten Nutzer
    haengen: Das Geraet bekaeme dessen Meldungen weiter auf den Sperrbildschirm,
    obwohl dort niemand mehr angemeldet ist.

    Geloescht wird nur ein Schluessel, der dem anfragenden Nutzer gehoert. Sonst
    koennte jeder mit einem fremden Schluessel dessen Zustellung abschalten.
    """
    from app.models.device_token import DeviceToken

    row = (await db.execute(
        select(DeviceToken).where(
            DeviceToken.token == body.token,
            DeviceToken.user_id == str(user.id),
        )
    )).scalar_one_or_none()
    if row:
        await db.delete(row)
        await db.commit()
    # Auch ohne Treffer Erfolg melden: Das Ziel — dieses Geraet bekommt keine
    # Meldungen dieses Nutzers mehr — ist dann bereits erreicht, und ein
    # Unterschied in der Antwort wuerde verraten, welche Schluessel es gibt.
    return {"status": "unregistered"}


# --- Web Push: das Browser-Gegenstueck zur Geraete-Registrierung oben ------------
# Gleiche Idee, andere Technik: statt eines APNs-Tokens meldet der Browser einen
# Endpunkt plus zwei Schluessel. Verschickt wird beides ueber denselben Verteilpunkt
# (core.push.push_to_user), damit keine Meldung nur eine Haelfte der Geraete erreicht.

class WebPushSubscribe(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


@router.get("/push/public-key")
async def webpush_public_key(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Der oeffentliche VAPID-Schluessel, den der Browser zum Anmelden braucht.

    Beim ersten Abruf wird das Schluesselpaar einmalig erzeugt und bleibt danach
    unveraendert — ein Wechsel wuerde alle bestehenden Anmeldungen entwerten.
    """
    from app.core.push_config import get_vapid_keys

    keys = await get_vapid_keys(db)
    return {"public_key": keys.public_b64 if keys else None}


@router.post("/push/subscribe", status_code=201)
async def webpush_subscribe(
    body: WebPushSubscribe,
    request: Request,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Browser-Anmeldung aufnehmen oder auffrischen.

    Der Endpunkt ist eindeutig: meldet sich derselbe Browser erneut an, wird der
    bestehende Eintrag uebernommen — sonst kaeme jede Meldung mehrfach an.
    """
    from app.models.push_subscription import PushSubscription

    existing = (await db.execute(
        select(PushSubscription).where(PushSubscription.endpoint == body.endpoint)
    )).scalar_one_or_none()
    if existing:
        existing.user_id = str(user.id)
        existing.p256dh = body.p256dh
        existing.auth = body.auth
    else:
        db.add(PushSubscription(
            user_id=str(user.id), endpoint=body.endpoint,
            p256dh=body.p256dh, auth=body.auth,
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        ))
    await db.commit()
    return {"status": "subscribed"}


@router.post("/push/unsubscribe")
async def webpush_unsubscribe(
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Abmelden. Nur die EIGENE Anmeldung — sonst koennte jeder Eingeloggte fremde
    Geraete stummschalten, indem er deren Endpunkt raet."""
    from app.models.push_subscription import PushSubscription

    endpoint = (body or {}).get("endpoint") or ""
    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint fehlt")
    sub = (await db.execute(
        select(PushSubscription).where(
            PushSubscription.endpoint == endpoint,
            PushSubscription.user_id == str(user.id),
        )
    )).scalar_one_or_none()
    if sub:
        await db.delete(sub)
        await db.commit()
    return {"status": "unsubscribed"}


# --- UI-facing: list, count, mark read ---

async def _visible_agent_ids(user, db: AsyncSession) -> list[str]:
    """Agent ids whose notifications this user may see: own + explicitly
    shared. Notifications are keyed by ``agent_id`` (no per-user recipient
    column), so without this scope every user would see every agent's
    notifications — a cross-user data leak.

    Ownerless agents are NOT auto-included (changed 2026-08-27, see
    tasks.py::_get_user_agent_ids) — they used to count as "system" agents
    visible to everyone, which leaked notifications the moment an agent
    was created without an assigned owner. is_platform_agent (explicit
    admin flag) is the deliberate replacement for that.
    """
    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess

    owned = (await db.execute(
        select(Agent.id).where(
            (Agent.user_id == user.id) | (Agent.is_platform_agent.is_(True))
        )
    )).scalars().all()
    shared = (await db.execute(
        select(AgentAccess.agent_id).where(AgentAccess.user_id == user.id)
    )).scalars().all()
    return list(set(owned) | set(shared))


@router.get("/")
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the UI notification center (scoped to the user's agents)."""
    visible = await _visible_agent_ids(user, db)
    query = select(Notification).where(Notification.agent_id.in_(visible))
    if unread_only:
        query = query.where(Notification.read == False)  # noqa: E712
    query = query.order_by(Notification.created_at.desc()).limit(limit)
    result = await db.execute(query)
    notifications = result.scalars().all()
    return {"notifications": [_to_response(n) for n in notifications]}


@router.get("/count")
async def unread_count(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Get unread notification count for the badge (scoped to the user's agents)."""
    visible = await _visible_agent_ids(user, db)
    result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.read == False,  # noqa: E712
            Notification.agent_id.in_(visible),
        )
    )
    count = result.scalar() or 0
    return {"unread": count}


class ApprovalResponse(BaseModel):
    choice: str


@router.post("/{notification_id}/respond")
async def respond_to_approval(
    notification_id: int,
    body: ApprovalResponse,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """User responds to an approval notification by picking one of the options."""
    result = await db.execute(select(Notification).where(Notification.id == notification_id))
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notif.agent_id not in await _visible_agent_ids(user, db):
        raise HTTPException(status_code=404, detail="Notification not found")
    if notif.type != "approval":
        raise HTTPException(status_code=400, detail="Not an approval notification")

    meta = dict(notif.meta or {})
    options = meta.get("options", [])
    if options and body.choice not in options:
        raise HTTPException(status_code=400, detail=f"Choice must be one of: {options}")

    meta["response"] = body.choice
    meta["responded_at"] = datetime.now(timezone.utc).isoformat()
    meta["responded_by"] = user.id if user.id != "__anonymous__" else None
    notif.meta = meta
    notif.read = True

    approval_id = meta.get("approval_id")
    if approval_id:
        try:
            from app.models.command_approval import ApprovalStatus, CommandApproval

            approval = await db.scalar(
                select(CommandApproval).where(CommandApproval.id == int(approval_id))
            )
            if approval and approval.status == ApprovalStatus.PENDING:
                negative = body.choice.lower() in {"deny", "denied", "no", "nein", "cancel", "abort", "ablehnen"}
                approval.status = ApprovalStatus.DENIED if negative else ApprovalStatus.APPROVED
                approval.resolved_at = datetime.now(timezone.utc)
                approval.user_response = body.choice
        except Exception as e:
            logger.warning(f"Could not update command approval {approval_id}: {e}")
    await db.commit()

    # Store result in Redis so waiting agent MCP call can pick it up
    try:
        if redis.client:
            await redis.client.set(f"approval:result:{notification_id}", body.choice, ex=3600)
            if approval_id:
                await redis.client.publish(
                    f"approval:{approval_id}",
                    json.dumps({
                        "status": "denied" if body.choice.lower() in {"deny", "denied", "no", "nein", "cancel", "abort", "ablehnen"} else "approved",
                        "approval_id": str(approval_id),
                        "reason": body.choice,
                    }),
                )
            await redis.client.publish(
                f"approval:response:{notification_id}",
                json.dumps({"choice": body.choice, "notification_id": notification_id}),
            )
    except Exception as e:
        logger.warning(f"Could not publish approval response: {e}")

    return {"status": "ok", "choice": body.choice}


@router.get("/{notification_id}/result")
async def get_approval_result(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Poll for an approval response (used by agents — no user auth required)."""
    result = await db.execute(select(Notification).where(Notification.id == notification_id))
    notif = result.scalar_one_or_none()
    if not notif or notif.type != "approval":
        raise HTTPException(status_code=404, detail="Approval not found")
    meta = notif.meta or {}
    return {
        "notification_id": notification_id,
        "choice": meta.get("response"),
        "status": "responded" if meta.get("response") else "pending",
    }


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int, user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Mark a notification as read."""
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id)
    )
    notif = result.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notif.agent_id not in await _visible_agent_ids(user, db):
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.read = True
    await db.commit()
    return {"status": "read"}


@router.post("/read-all")
async def mark_all_read(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Mark all of the user's notifications as read (scoped to the user's agents)."""
    visible = await _visible_agent_ids(user, db)
    await db.execute(
        update(Notification)
        .where(
            Notification.read == False,  # noqa: E712
            Notification.agent_id.in_(visible),
        )
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
    if notif.agent_id not in await _visible_agent_ids(user, db):
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.delete(notif)
    await db.commit()
    return {"deleted": notification_id}


async def _send_telegram(body: NotificationCreate, redis: RedisService, notif_id: int | None = None) -> None:
    """Send high-priority notifications via Telegram.

    Routes to:
    1. Per-agent Telegram bot (if configured) -> all authorized users
    2. Global Telegram bot (fallback) -> admin chat
    For approval notifications: sends with inline keyboard for the options.
    """
    # Build inline keyboard for approval notifications
    reply_markup = None
    if body.type == "approval" and notif_id and body.meta and isinstance(body.meta.get("options"), list):
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            options = body.meta["options"]
            buttons = [
                [InlineKeyboardButton(opt, callback_data=f"approval:{notif_id}:{opt}")]
                for opt in options[:5]
            ]
            reply_markup = InlineKeyboardMarkup(buttons)
        except Exception:
            reply_markup = None

    emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅", "approval": "❓"}.get(body.type, "📢")
    text = f"{emoji} *{body.title}*"
    if body.message:
        text += f"\n\n{body.message}"

    # 1. Try per-agent bot first
    try:
        import app.main as main_mod
        tg_manager = getattr(main_mod.app.state, "telegram_bot_manager", None)
        if tg_manager and body.agent_id:
            bot = tg_manager.get_bot(body.agent_id)
            if bot and bot._started:
                await bot.send_to_all_authorized(text, reply_markup=reply_markup)
                return  # Sent via agent bot, no need for global bot
    except Exception:
        logger.warning("[Notify] Agent bot delivery failed, falling back to global bot", exc_info=True)

    # 2. Fallback: global bot. The only subscriber is TelegramBot._listen_notifications,
    # which reads "text" off the singular `telegram:notification` channel.
    try:
        from app.config import settings
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            return
        await redis.client.publish("telegram:notification", json.dumps({
            "text": text,
            "parse_mode": "Markdown",
            "priority": body.priority,
            "agent_id": body.agent_id,
        }))
    except Exception:
        logger.warning("[Notify] Global bot fallback delivery failed", exc_info=True)


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


def _push_payload(n: Notification) -> dict:
    meta = n.meta or {}
    payload = {
        "notification_id": str(n.id),
        "agent_id": n.agent_id,
        "type": n.type,
        "action_url": n.action_url or "",
        "meta": meta,
    }
    if isinstance(meta, dict):
        task_id = meta.get("task_id")
        session_id = meta.get("session_id")
        message_id = meta.get("message_id")
        if task_id:
            payload["task_id"] = str(task_id)
        if session_id:
            payload["session_id"] = str(session_id)
        if message_id:
            payload["message_id"] = str(message_id)
    return payload
