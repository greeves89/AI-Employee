"""Kiosk API — read-only live status + local agent chat for the on-Pi kiosk display.

SECURITY MODEL
--------------
These endpoints are intentionally UNAUTHENTICATED so the local kiosk browser
(a fullscreen Chromium on the Pi, never logged in) can render live status and
talk to agents. They are made reachable ONLY from the device itself: the Caddy
reverse-proxy returns 404 for any ``/api/v1/kiosk*`` request that arrives through
the Cloudflare tunnel (identified by the ``Cf-Ray`` header). Requests from the
Pi's own browser hit Caddy directly and have no such header, so they pass.
Never expose these endpoints beyond the local device.

REUSE (no parallel implementations)
-----------------------------------
* Status is aggregated read-only from the same models the app uses (Agent, Task,
  ChatMessage) plus host metrics written by the host power collector
  (scripts/kiosk-power-collector.sh) and bind-mounted read-only at
  ``settings.kiosk_metrics_path``.
* Chat reuses the exact same Redis queue (``agent:{id}:chat``) that the
  authenticated WebSocket chat pushes to, and the same ``chat_messages``
  persistence: the background ``[ChatPersist]`` subscriber in main.py links the
  agent's reply to the user message by ``message_id`` -> ``session_id``. So the
  kiosk persists the user message (exactly like the WS does) and then polls the
  chat history for the reply.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.dependencies import get_redis_service
from app.models.agent import Agent
from app.models.chat_message import ChatMessage
from app.models.task import Task, TaskStatus
from app.services.redis_service import RedisService

router = APIRouter(prefix="/kiosk", tags=["kiosk"])


def _state_value(state) -> str:
    return state.value if hasattr(state, "value") else str(state)


def _read_host_metrics() -> dict:
    """Host metrics written by the host power collector; {} if unavailable."""
    try:
        with open(settings.kiosk_metrics_path) as f:
            data = json.load(f)
        data["stale"] = (time.time() - float(data.get("ts", 0))) > 30
        return data
    except Exception:
        return {}


@router.get("/overview")
async def kiosk_overview(db: AsyncSession = Depends(get_db)):
    """Everything the kiosk dashboard renders: agents, tasks, AI spend, Pi, power."""
    now = datetime.now(timezone.utc)
    start_today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    # --- Agents (with current task for the busy ones) ---
    agents = (await db.execute(select(Agent))).scalars().all()
    agent_list: list[dict] = []
    working = 0
    for a in agents:
        st = _state_value(a.state)
        current = None
        if st in ("working", "running"):
            working += 1
            row = (await db.execute(
                select(Task.title)
                .where(Task.agent_id == a.id, Task.status == TaskStatus.RUNNING)
                .order_by(Task.created_at.desc())
                .limit(1)
            )).first()
            current = row[0] if row else None
        agent_list.append({
            "id": a.id,
            "name": a.name,
            "state": st,
            "model": a.model,
            "current_task": current,
            "has_container": bool(a.container_id),
        })

    # --- Task counters + recent feed ---
    running = await db.scalar(
        select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING)
    )
    pending = await db.scalar(
        select(func.count()).select_from(Task).where(
            Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED])
        )
    )
    done_today = await db.scalar(
        select(func.count()).select_from(Task).where(
            Task.status == TaskStatus.COMPLETED, Task.completed_at >= start_today
        )
    )
    recent_rows = (await db.execute(
        select(Task.title, Task.status, Task.agent_id)
        .order_by(Task.created_at.desc())
        .limit(6)
    )).all()
    recent = [
        {"title": r[0], "status": _state_value(r[1]), "agent_id": r[2]}
        for r in recent_rows
    ]

    # --- AI spend today (tasks + chat) ---
    task_cost = await db.scalar(
        select(func.coalesce(func.sum(Task.cost_usd), 0.0)).where(Task.created_at >= start_today)
    ) or 0.0
    tokens_in = await db.scalar(
        select(func.coalesce(func.sum(Task.input_tokens), 0)).where(Task.created_at >= start_today)
    ) or 0
    tokens_out = await db.scalar(
        select(func.coalesce(func.sum(Task.output_tokens), 0)).where(Task.created_at >= start_today)
    ) or 0
    chat_cost = await db.scalar(
        select(func.coalesce(func.sum(ChatMessage.cost_usd), 0.0)).where(ChatMessage.timestamp >= start_today)
    ) or 0.0

    # --- Host + live power / electricity cost ---
    host = _read_host_metrics()
    price = settings.electricity_price_eur_kwh
    watts = host.get("power_w")
    today_kwh = host.get("today_kwh")
    power = {
        "watts": watts,
        "today_kwh": today_kwh,
        "today_cost_eur": round(today_kwh * price, 3) if today_kwh is not None else None,
        # naive monthly projection from the current draw
        "month_cost_eur": round((watts / 1000.0) * 24 * 30 * price, 2) if watts is not None else None,
        "price_eur_kwh": price,
    }
    pi = {
        "temp_c": host.get("temp_c"),
        "cpu_percent": host.get("cpu_percent"),
        "load": host.get("load"),
        "mem_used_mb": host.get("mem_used_mb"),
        "mem_total_mb": host.get("mem_total_mb"),
        "disk_used_gb": host.get("disk_used_gb"),
        "disk_total_gb": host.get("disk_total_gb"),
        "uptime_s": host.get("uptime_s"),
        "stale": host.get("stale", True),
    }

    return {
        "ts": now.isoformat(),
        "agents": agent_list,
        "agents_working": working,
        "agents_total": len(agents),
        "tasks": {
            "running": int(running or 0),
            "pending": int(pending or 0),
            "done_today": int(done_today or 0),
            "recent": recent,
        },
        "ai_spend": {
            "cost_usd_today": round(float(task_cost) + float(chat_cost), 4),
            "tokens_in_today": int(tokens_in),
            "tokens_out_today": int(tokens_out),
        },
        "pi": pi,
        "power": power,
    }


# --------------------------------------------------------------------------- #
# Kiosk chat (local-only) — reuses the agent's chat queue + chat_messages       #
# --------------------------------------------------------------------------- #

class KioskChatSend(BaseModel):
    text: str
    session_id: str | None = None


@router.post("/chat/{agent_id}")
async def kiosk_chat_send(
    agent_id: str,
    body: KioskChatSend,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Send a message to an agent from the kiosk.

    Persists the user message (so the [ChatPersist] subscriber can attach the
    reply to the same session), then pushes onto the same Redis queue the
    WebSocket chat uses.
    """
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.container_id:
        raise HTTPException(status_code=409, detail="Agent has no running container")

    session_id = body.session_id or uuid.uuid4().hex[:12]
    message_id = uuid.uuid4().hex[:12]

    db.add(ChatMessage(
        agent_id=agent_id,
        session_id=session_id,
        message_id=message_id,
        role="user",
        content=text,
    ))
    await db.commit()

    await redis.client.lpush(
        f"agent:{agent_id}:chat",
        json.dumps({
            "id": message_id,
            "text": text,
            "source": "webapp",
            "chat_session_id": session_id,
        }),
    )
    return {"session_id": session_id, "message_id": message_id}


@router.get("/chat/{agent_id}/history")
async def kiosk_chat_history(
    agent_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Poll the persisted conversation for a kiosk chat session."""
    rows = (await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id)
        .order_by(ChatMessage.timestamp.asc(), ChatMessage.id.asc())
        .limit(100)
    )).scalars().all()
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "message_id": m.message_id,
                "ts": m.timestamp.isoformat() if m.timestamp else None,
            }
            for m in rows
            if m.role in ("user", "assistant", "error")
        ],
    }
