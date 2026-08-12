"""Webhook API - external services can trigger agent tasks."""

import hashlib
import hmac
import json
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_auth
from app.models.agent import Agent
from app.models.feedback import Feedback, FeedbackStatus
from app.models.task import Task, TaskStatus
from app.models.webhook import WebhookEvent
from app.security.agent_guard import (
    check_webhook_payload,
    notify_security_block,
    sanitize_webhook_payload,
    webhook_rate_limiter,
)
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


async def _start_workflow_from_trigger(
    db: AsyncSession, trigger, prompt: str, payload: dict, source: str, event_type: str
) -> dict | None:
    """Einen Workflow-Lauf aus einem Webhook-Auslöser starten (#392).

    Gibt die Kennungen zurück oder ``None``, wenn der Workflow fehlt oder
    abgeschaltet ist — dann fällt der Aufrufer bewusst auf den Auftragsweg zurück,
    statt den Auslöser stillschweigend zu verschlucken.

    Die Nutzlast landet unter ``trigger`` im Lauf-Kontext. Damit greift die
    vorhandene Platzhalter-Ersetzung des Motors (``{{trigger}}``) ohne eine zweite
    Ersetzungslogik, und der aus dem Auslöser bereits gefüllte Prompt steht unter
    ``trigger_prompt`` bereit.
    """
    from app.models.workflow import Workflow
    from app.services.workflow_engine import start_run

    workflow = (await db.execute(
        select(Workflow).where(Workflow.id == trigger.workflow_id)
    )).scalar_one_or_none()
    if workflow is None or not workflow.enabled:
        return None

    run = await start_run(workflow, db, context={
        "trigger": {"result": json.dumps(payload, ensure_ascii=False)[:8000]},
        "trigger_prompt": {"result": prompt},
        "trigger_source": {"result": source},
        "trigger_event": {"result": event_type},
    })
    logger.info("[Webhook] Trigger %s startete Workflow-Lauf %s", trigger.name, run.id)
    return {
        "workflow_id": workflow.id,
        "run_id": run.id,
        "trigger_id": trigger.id,
        "trigger_name": trigger.name,
    }

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookTrigger(BaseModel):
    source: str = "custom"
    event_type: str = "generic"


# --- Per-agent webhook settings ---

async def _assert_agent_owned(agent_id: str, user, db) -> None:
    """404 unless the caller owns/shares the agent (admin bypass). Defense-in-depth
    beyond RLS — these endpoints disclose/rotate the secret webhook_token."""
    from app.core.ownership import visible_agent_ids
    vids = await visible_agent_ids(user, db)
    if vids is not None and agent_id not in vids:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.get("/agents/{agent_id}/settings")
async def get_webhook_settings(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get webhook settings for an agent."""
    await _assert_agent_owned(agent_id, user, db)
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
    await _assert_agent_owned(agent_id, user, db)
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
    await _assert_agent_owned(agent_id, user, db)
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
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Receive a webhook event and create a task for the specified agent.

    The full request body is passed as context to the agent.
    Supports JSON, form data, or raw text.
    Auth: Authorization: Bearer <webhook_token> (per-agent token from settings).
    """
    # CORS for cross-origin tool hosts (Open WebUI); token-authed, no cookies.
    response.headers.update(_WEBHOOK_CORS)
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

    workflows_started = []

    if triggers:
        # Fire each matching trigger
        for trigger in triggers:
            prompt = await fire_trigger(trigger, payload, str(source), str(event_type), db)

            # Ziel Workflow statt Einzelauftrag (#392). Alles davor — Treffer,
            # Bedingungen, Sicherheitspruefung der Nutzlast, Zaehler — ist
            # identisch; nur was ausgeloest wird, unterscheidet sich.
            if getattr(trigger, "workflow_id", None):
                started = await _start_workflow_from_trigger(
                    db, trigger, prompt, payload, str(source), str(event_type)
                )
                if started:
                    workflows_started.append(started)
                    continue
                # Kein Workflow gefunden oder abgeschaltet: NICHT stillschweigend
                # nichts tun. Der Auftragsweg unten ist der ehrlichere Ausgang —
                # ein verschluckter Ausloeser ist genau die Sorte Fehler, die
                # niemand bemerkt, bis sie teuer wird.
                logger.warning(
                    "[Webhook] Trigger %s zeigt auf Workflow %s — nicht nutzbar, "
                    "es wird ersatzweise ein Auftrag angelegt",
                    trigger.name, trigger.workflow_id,
                )

            task_id = uuid.uuid4().hex[:12]
            title = f"Trigger: {trigger.name} ({source}/{event_type})"
            task = Task(
                id=task_id,
                title=title,
                prompt=prompt,
                status=TaskStatus.QUEUED,
                agent_id=agent_id,
                model=trigger.model,
                priority=trigger.priority,
                metadata_={"source": "webhook", "trigger": trigger.name},
            )
            db.add(task)
            task_payload = json.dumps({
                "id": task_id,
                "prompt": prompt,
                "title": title,
                "model": trigger.model,
                "priority": trigger.priority,
            })
            await redis.client.lpush(f"agent:{agent_id}:tasks", task_payload)
            tasks_created.append({"task_id": task_id, "trigger_id": trigger.id, "trigger_name": trigger.name})
    else:
        # No triggers defined — fall back to default behavior (create task with raw payload)
        prompt = sanitize_webhook_payload(payload, str(source), str(event_type))
        task_id = uuid.uuid4().hex[:12]
        title = f"Webhook: {source}/{event_type}"
        task = Task(
            id=task_id,
            title=title,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            agent_id=agent_id,
            metadata_={"source": "webhook"},
        )
        db.add(task)
        task_payload = json.dumps({
            "id": task_id,
            "prompt": prompt,
            "title": title,
            "model": None,
        })
        await redis.client.lpush(f"agent:{agent_id}:tasks", task_payload)
        tasks_created.append({"task_id": task_id, "trigger_id": None, "trigger_name": None})

    # Update webhook event with first task link. Seit #392 kann ein Auslöser
    # statt eines Auftrags einen Workflow starten — dann gibt es hier keinen
    # Auftrag zu verknuepfen, und ein blinder Zugriff auf [0] wuerde den ganzen
    # Webhook mit einem IndexError beantworten.
    if tasks_created:
        event.task_id = tasks_created[0]["task_id"]
    await db.commit()

    return {
        "status": "accepted",
        "webhook_event_id": event.id,
        "tasks": tasks_created,
        "workflows": workflows_started,
        "agent_id": agent_id,
    }


# --- OpenAPI tool-server surface (e.g. Open WebUI) -------------------------- #
# CORS is permissive here on purpose: these endpoints are token-authenticated
# (Bearer), carry no cookies, and are meant to be consumed cross-origin by an
# external tool host. `*` without credentials is safe for that.
_WEBHOOK_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Webhook-Source, X-Webhook-Event",
    "Access-Control-Max-Age": "600",
}


def _public_base(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


@router.options("/agents/{agent_id}")
@router.options("/agents/{agent_id}/openapi.json")
async def webhook_cors_preflight(agent_id: str):
    """CORS preflight for the OpenAPI tool-server endpoints."""
    return Response(status_code=204, headers=_WEBHOOK_CORS)


@router.get("/agents/{agent_id}/openapi.json")
async def webhook_openapi(agent_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """OpenAPI 3.1 spec for THIS agent's webhook so it can be registered as an
    OpenAPI tool server (Open WebUI etc.). Describes the single POST operation
    that hands a message/event to the agent.

    Note: consume this over the PUBLIC HTTPS URL, not an internal http:// URL —
    a browser tool host on https would otherwise block it as mixed content.
    """
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        return JSONResponse({"detail": "Agent not found"}, status_code=404, headers=_WEBHOOK_CORS)
    if not agent.webhook_enabled:
        return JSONResponse(
            {"detail": "Webhook access is not enabled for this agent"},
            status_code=403, headers=_WEBHOOK_CORS,
        )
    base = _public_base(request)
    path = f"/webhooks/agents/{agent_id}"
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": f"AI-Employee Agent Webhook — {agent.name}",
            "description": (
                "Send a message or event to this AI-Employee agent; it turns the payload "
                "into a task and works on it. Use this as a tool to delegate work to the agent."
            ),
            "version": "1.0.0",
        },
        "servers": [{"url": f"{base}/api/v1"}],
        "components": {
            "securitySchemes": {
                "webhookToken": {
                    "type": "http", "scheme": "bearer",
                    "description": "The agent's webhook token (from the agent's webhook settings).",
                }
            }
        },
        "security": [{"webhookToken": []}],
        "paths": {
            path: {
                "post": {
                    "operationId": "send_to_agent",
                    "summary": f"Send a message/task to the agent {agent.name}",
                    "description": (
                        "Hands the agent a message/event. The agent creates a task from it "
                        "and processes it."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "message": {"type": "string", "description": "The instruction/message for the agent."},
                                        "source": {"type": "string", "description": "Optional source label (e.g. 'openwebui')."},
                                        "event_type": {"type": "string", "description": "Optional event type."},
                                    },
                                    "required": ["message"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "Accepted — task(s) created.",
                                "content": {"application/json": {"schema": {"type": "object"}}}},
                        "401": {"description": "Invalid or missing webhook token."},
                        "403": {"description": "Webhook not enabled for this agent."},
                        "429": {"description": "Rate limit exceeded."},
                    },
                }
            }
        },
    }
    return JSONResponse(spec, headers=_WEBHOOK_CORS)


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
    await _assert_agent_owned(agent_id, user, db)
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


# --- WhatsApp (Meta Cloud API) -------------------------------------------------
# Der einzige Kanal, der NICHT abgefragt werden kann: Meta stellt ausschliesslich
# per Webhook zu. Diese Adresse ist damit oeffentlich erreichbar — deshalb wird
# jede Zustellung gegen die Signatur geprueft, bevor irgendetwas passiert.

@router.get("/whatsapp")
async def whatsapp_verify(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Einrichtungs-Prueffrage von Meta beantworten.

    Meta schickt beim Verbinden ein ``hub.challenge`` und erwartet es unveraendert
    zurueck — aber nur, wenn das mitgeschickte Token zu dem passt, das der
    Administrator hinterlegt hat.
    """
    from fastapi.responses import PlainTextResponse
    from app.services.settings_service import SettingsService

    params = request.query_params
    expected = await SettingsService(db).get("whatsapp_verify_token")
    if params.get("hub.mode") == "subscribe" and expected and \
            params.get("hub.verify_token") == expected:
        return PlainTextResponse(params.get("hub.challenge") or "")
    logger.warning("[WhatsApp] Verifizierung abgelehnt")
    raise HTTPException(status_code=403, detail="Verifizierung fehlgeschlagen")


@router.post("/whatsapp")
async def whatsapp_inbound(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Eingehende WhatsApp-Nachrichten an die Agenten weiterreichen.

    Antwortet IMMER mit 200, sobald die Signatur stimmt: Meta wiederholt sonst die
    Zustellung und stellt den Webhook nach wiederholten Fehlern ganz ab. Was intern
    schiefgeht, gehoert ins Log, nicht in den Statuscode.
    """
    from app.services import whatsapp_gateway as wa
    from app.services.settings_service import SettingsService

    body = await request.body()
    secret = await SettingsService(db).get("whatsapp_app_secret") or ""
    signature = request.headers.get("x-hub-signature-256", "")
    if not wa.verify_signature(body, signature, secret):
        logger.warning("[WhatsApp] Zustellung mit ungueltiger Signatur abgelehnt")
        raise HTTPException(status_code=403, detail="Signatur ungueltig")

    try:
        payload = json.loads(body)
        delivered = await wa.handle_payload(request.app.state.redis, payload)
        if delivered:
            logger.info("[WhatsApp] %s Nachricht(en) zugestellt", delivered)
    except Exception as e:  # noqa: BLE001 — nie 5xx an Meta zurueckgeben
        logger.warning("[WhatsApp] Verarbeitung fehlgeschlagen: %s", e)
    return {"status": "ok"}
