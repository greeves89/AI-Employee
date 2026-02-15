"""Webhook API - external services can trigger agent tasks."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service
from app.models.agent import Agent
from app.models.webhook import WebhookEvent
from app.services.redis_service import RedisService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookTrigger(BaseModel):
    source: str = "custom"
    event_type: str = "generic"
    prompt_template: str | None = None  # optional: override the default prompt


@router.post("/agents/{agent_id}")
async def receive_webhook(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Receive a webhook event and create a task for the specified agent.

    The full request body is passed as context to the agent.
    Supports JSON, form data, or raw text.
    """
    # Verify agent exists
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Parse payload
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {"raw": (await request.body()).decode("utf-8", errors="replace")}
    else:
        body = await request.body()
        payload = {"raw": body.decode("utf-8", errors="replace")}

    # Extract source/event from headers or payload
    source = (
        request.headers.get("x-webhook-source")
        or request.headers.get("x-github-event", "")
        or payload.get("source", "external")
    )
    event_type = (
        request.headers.get("x-webhook-event")
        or request.headers.get("x-github-event", "")
        or payload.get("event_type", "generic")
    )

    # Save webhook event
    event = WebhookEvent(
        agent_id=agent_id,
        source=str(source),
        event_type=str(event_type),
        payload=payload,
        status="processing",
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    # Build prompt for the agent
    prompt_template = payload.get("prompt_template")
    if prompt_template:
        prompt = prompt_template.replace("{{payload}}", json.dumps(payload, indent=2))
    else:
        prompt = (
            f"Webhook Event received:\n"
            f"Source: {source}\n"
            f"Event: {event_type}\n"
            f"Payload:\n```json\n{json.dumps(payload, indent=2)}\n```\n\n"
            f"Process this event according to your role and knowledge. "
            f"If you're unsure what to do, send a notification to the user."
        )

    # Create task via Redis queue
    task_id = uuid.uuid4().hex[:12]
    task_payload = json.dumps({
        "id": task_id,
        "prompt": prompt,
        "title": f"Webhook: {source}/{event_type}",
        "model": None,
    })
    await redis.client.lpush(f"agent:{agent_id}:tasks", task_payload)

    # Update webhook event with task link
    event.task_id = task_id
    await db.commit()

    return {
        "status": "accepted",
        "webhook_event_id": event.id,
        "task_id": task_id,
        "agent_id": agent_id,
    }


@router.get("/agents/{agent_id}/events")
async def list_webhook_events(
    agent_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List webhook events for an agent."""
    query = (
        select(WebhookEvent)
        .where(WebhookEvent.agent_id == agent_id)
        .order_by(WebhookEvent.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    events = result.scalars().all()
    return {
        "events": [
            {
                "id": e.id,
                "source": e.source,
                "event_type": e.event_type,
                "status": e.status,
                "task_id": e.task_id,
                "created_at": e.created_at.isoformat() if e.created_at else "",
            }
            for e in events
        ]
    }
