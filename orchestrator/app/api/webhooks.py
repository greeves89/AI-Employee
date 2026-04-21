"""Webhook API - external services can trigger agent tasks."""

import hashlib
import hmac
import json
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth
from app.models.agent import Agent
from app.models.feedback import Feedback, FeedbackStatus
from app.models.webhook import WebhookEvent
from app.security.agent_guard import (
    check_webhook_payload,
    notify_security_block,
    sanitize_webhook_payload,
    webhook_rate_limiter,
)
from app.services.redis_service import RedisService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookTrigger(BaseModel):
    source: str = "custom"
    event_type: str = "generic"


# --- Per-agent webhook settings ---

@router.get("/agents/{agent_id}/settings")
async def get_webhook_settings(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get webhook settings for an agent."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "webhook_enabled": agent.webhook_enabled,
        "webhook_token": agent.webhook_token if agent.webhook_enabled else None,
    }


@router.patch("/agents/{agent_id}/settings")
async def update_webhook_settings(
    agent_id: str,
    body: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable webhook access for an agent. Generates a token on first enable."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    enabled = body.get("webhook_enabled")
    if enabled is not None:
        agent.webhook_enabled = bool(enabled)
        if enabled and not agent.webhook_token:
            agent.webhook_token = secrets.token_urlsafe(32)

    await db.commit()
    return {
        "webhook_enabled": agent.webhook_enabled,
        "webhook_token": agent.webhook_token if agent.webhook_enabled else None,
    }


@router.post("/agents/{agent_id}/regenerate-token")
async def regenerate_webhook_token(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new webhook token for an agent."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.webhook_enabled:
        raise HTTPException(status_code=400, detail="Webhook is not enabled for this agent")

    agent.webhook_token = secrets.token_urlsafe(32)
    await db.commit()
    return {"webhook_token": agent.webhook_token}


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
    Auth: Authorization: Bearer <webhook_token> (per-agent token from settings).
    """
    # --- AgentGuard: Rate limiting ---
    if not webhook_rate_limiter.check(agent_id):
        raise HTTPException(status_code=429, detail="Rate limit exceeded for this agent")

    # Verify agent exists
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # --- Per-agent token auth ---
    if not agent.webhook_enabled:
        raise HTTPException(status_code=403, detail="Webhook access is not enabled for this agent")

    if agent.webhook_token:
        auth_header = request.headers.get("Authorization", "")
        provided_token = auth_header.removeprefix("Bearer ").strip()
        if not provided_token or not hmac.compare_digest(provided_token, agent.webhook_token):
            raise HTTPException(status_code=401, detail="Invalid or missing webhook token")

    body_bytes = await request.body()

    # Parse payload
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            payload = json.loads(body_bytes)
        except Exception:
            payload = {"raw": body_bytes.decode("utf-8", errors="replace")}
    else:
        payload = {"raw": body_bytes.decode("utf-8", errors="replace")}

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

    # --- Security Layer: check payload for injection ---
    verdict = check_webhook_payload(payload, str(source))
    if not verdict.allowed:
        # Block the webhook, log it, and notify user
        event = WebhookEvent(
            agent_id=agent_id,
            source=str(source),
            event_type=str(event_type),
            payload=payload,
            status="blocked",
        )
        db.add(event)
        await db.commit()
        await notify_security_block(
            redis.client, source=f"webhook/{source}", reason=verdict.reason, agent_id=agent_id
        )
        raise HTTPException(
            status_code=403,
            detail=f"Blocked by security layer: {verdict.reason}",
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

    # --- Check EventTriggers for conditional routing ---
    from app.services.trigger_evaluator import find_matching_triggers, fire_trigger

    triggers = await find_matching_triggers(db, agent_id, str(source), str(event_type), payload)

    tasks_created = []

    if triggers:
        # Fire each matching trigger
        for trigger in triggers:
            prompt = await fire_trigger(trigger, payload, str(source), str(event_type), db)
            task_id = uuid.uuid4().hex[:12]
            task_payload = json.dumps({
                "id": task_id,
                "prompt": prompt,
                "title": f"Trigger: {trigger.name} ({source}/{event_type})",
                "model": trigger.model,
                "priority": trigger.priority,
            })
            await redis.client.lpush(f"agent:{agent_id}:tasks", task_payload)
            tasks_created.append({"task_id": task_id, "trigger_id": trigger.id, "trigger_name": trigger.name})
    else:
        # No triggers defined — fall back to default behavior (create task with raw payload)
        prompt = sanitize_webhook_payload(payload, str(source), str(event_type))
        task_id = uuid.uuid4().hex[:12]
        task_payload = json.dumps({
            "id": task_id,
            "prompt": prompt,
            "title": f"Webhook: {source}/{event_type}",
            "model": None,
        })
        await redis.client.lpush(f"agent:{agent_id}:tasks", task_payload)
        tasks_created.append({"task_id": task_id, "trigger_id": None, "trigger_name": None})

    # Update webhook event with first task link
    event.task_id = tasks_created[0]["task_id"]
    await db.commit()

    return {
        "status": "accepted",
        "webhook_event_id": event.id,
        "tasks": tasks_created,
        "agent_id": agent_id,
    }


@router.post("/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Receive GitHub webhook events and sync feedback status.

    Listens for 'issues' events: when a GitHub issue linked to feedback
    is closed, the feedback status is automatically updated to 'closed'.
    """
    from app.config import settings as app_settings

    # Verify HMAC signature if secret is configured
    secret = app_settings.github_webhook_secret
    if secret:
        sig_header = request.headers.get("x-hub-signature-256", "")
        body = await request.body()
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        body = await request.body()

    event_type = request.headers.get("x-github-event", "")
    if event_type != "issues":
        return {"status": "ignored", "reason": "not an issues event"}

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    action = payload.get("action")
    issue = payload.get("issue", {})
    issue_url = issue.get("html_url", "")

    if action != "closed" or not issue_url:
        return {"status": "ignored", "reason": f"action={action}"}

    # Find feedback linked to this GitHub issue
    result = await db.execute(
        select(Feedback).where(Feedback.github_issue_url == issue_url)
    )
    feedback = result.scalar_one_or_none()

    if not feedback:
        return {"status": "ignored", "reason": "no feedback linked to this issue"}

    feedback.status = FeedbackStatus.CLOSED
    await db.commit()

    return {"status": "updated", "feedback_id": feedback.id, "new_status": "closed"}


@router.get("/agents/{agent_id}/events")
async def list_webhook_events(
    agent_id: str,
    limit: int = 50,
    user=Depends(require_auth),
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
