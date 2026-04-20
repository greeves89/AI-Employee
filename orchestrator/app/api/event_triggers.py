"""Event Triggers API - CRUD for webhook-to-task routing rules."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.event_trigger import EventTrigger

router = APIRouter(prefix="/event-triggers", tags=["event-triggers"])


# --- Pydantic schemas ---

class EventTriggerCreate(BaseModel):
    name: str
    agent_id: str
    source_filter: str | None = None
    event_type_filter: str | None = None
    payload_conditions: dict | None = None
    prompt_template: str
    priority: int = 5
    model: str | None = None
    enabled: bool = True


class EventTriggerUpdate(BaseModel):
    name: str | None = None
    source_filter: str | None = None
    event_type_filter: str | None = None
    payload_conditions: dict | None = None
    prompt_template: str | None = None
    priority: int | None = None
    model: str | None = None
    enabled: bool | None = None


def _to_response(t: EventTrigger) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "agent_id": t.agent_id,
        "source_filter": t.source_filter,
        "event_type_filter": t.event_type_filter,
        "payload_conditions": t.payload_conditions,
        "prompt_template": t.prompt_template,
        "priority": t.priority,
        "model": t.model,
        "enabled": t.enabled,
        "fire_count": t.fire_count,
        "last_fired_at": t.last_fired_at.isoformat() if t.last_fired_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


# --- Endpoints ---

@router.get("")
async def list_triggers(
    agent_id: str | None = Query(None),
    enabled: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List event triggers with optional filtering."""
    query = select(EventTrigger)
    if agent_id:
        query = query.where(EventTrigger.agent_id == agent_id)
    if enabled is not None:
        query = query.where(EventTrigger.enabled == enabled)

    total_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_q)).scalar() or 0

    query = query.order_by(EventTrigger.priority.desc(), EventTrigger.created_at.desc())
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    triggers = result.scalars().all()

    return {"triggers": [_to_response(t) for t in triggers], "total": total}


@router.get("/{trigger_id}")
async def get_trigger(
    trigger_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get a single event trigger by ID."""
    result = await db.execute(select(EventTrigger).where(EventTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Event trigger not found")
    return _to_response(trigger)


@router.post("", status_code=201)
async def create_trigger(
    body: EventTriggerCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new event trigger."""
    trigger = EventTrigger(
        name=body.name,
        agent_id=body.agent_id,
        source_filter=body.source_filter,
        event_type_filter=body.event_type_filter,
        payload_conditions=body.payload_conditions,
        prompt_template=body.prompt_template,
        priority=body.priority,
        model=body.model,
        enabled=body.enabled,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return _to_response(trigger)


@router.put("/{trigger_id}")
async def update_trigger(
    trigger_id: int,
    body: EventTriggerUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update an event trigger."""
    result = await db.execute(select(EventTrigger).where(EventTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Event trigger not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(trigger, field, value)

    await db.commit()
    await db.refresh(trigger)
    return _to_response(trigger)


@router.delete("/{trigger_id}")
async def delete_trigger(
    trigger_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete an event trigger."""
    result = await db.execute(select(EventTrigger).where(EventTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Event trigger not found")
    await db.delete(trigger)
    await db.commit()
    return {"deleted": trigger_id}


@router.post("/{trigger_id}/toggle")
async def toggle_trigger(
    trigger_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Toggle an event trigger's enabled state."""
    result = await db.execute(select(EventTrigger).where(EventTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Event trigger not found")
    trigger.enabled = not trigger.enabled
    await db.commit()
    await db.refresh(trigger)
    return _to_response(trigger)


# --- Agent-accessible endpoints (HMAC auth) ---


@router.get("/for-agent")
async def list_triggers_for_agent(
    agent_info=Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """List triggers owned by this agent (HMAC auth)."""
    agent_id = agent_info["agent_id"]
    query = select(EventTrigger).where(EventTrigger.agent_id == agent_id)
    query = query.order_by(EventTrigger.priority.desc(), EventTrigger.created_at.desc())
    result = await db.execute(query)
    triggers = result.scalars().all()
    return {"triggers": [_to_response(t) for t in triggers], "total": len(triggers)}


@router.post("/for-agent", status_code=201)
async def create_trigger_for_agent(
    body: EventTriggerCreate,
    agent_info=Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Create a trigger for this agent (HMAC auth). agent_id is forced to caller's ID."""
    trigger = EventTrigger(
        name=body.name,
        agent_id=agent_info["agent_id"],
        source_filter=body.source_filter,
        event_type_filter=body.event_type_filter,
        payload_conditions=body.payload_conditions,
        prompt_template=body.prompt_template,
        priority=body.priority,
        model=body.model,
        enabled=body.enabled,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    return _to_response(trigger)


@router.delete("/for-agent/{trigger_id}")
async def delete_trigger_for_agent(
    trigger_id: int,
    agent_info=Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete a trigger owned by this agent (HMAC auth)."""
    result = await db.execute(
        select(EventTrigger).where(
            EventTrigger.id == trigger_id,
            EventTrigger.agent_id == agent_info["agent_id"],
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found or not owned by this agent")
    await db.delete(trigger)
    await db.commit()
    return {"deleted": trigger_id}


@router.patch("/for-agent/{trigger_id}/toggle")
async def toggle_trigger_for_agent(
    trigger_id: int,
    agent_info=Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Toggle a trigger's enabled state (HMAC auth)."""
    result = await db.execute(
        select(EventTrigger).where(
            EventTrigger.id == trigger_id,
            EventTrigger.agent_id == agent_info["agent_id"],
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found or not owned by this agent")
    trigger.enabled = not trigger.enabled
    await db.commit()
    await db.refresh(trigger)
    return _to_response(trigger)


@router.post("/{trigger_id}/test")
async def test_trigger(
    trigger_id: int,
    payload: dict,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Test a trigger against a sample payload without creating a task.

    Returns whether the trigger would match and the interpolated prompt.
    """
    from app.services.trigger_evaluator import interpolate_template, match_conditions

    result = await db.execute(select(EventTrigger).where(EventTrigger.id == trigger_id))
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Event trigger not found")

    conditions_match = True
    if trigger.payload_conditions:
        conditions_match = match_conditions(payload, trigger.payload_conditions)

    interpolated_prompt = interpolate_template(trigger.prompt_template, payload)

    return {
        "trigger_id": trigger.id,
        "would_fire": conditions_match,
        "interpolated_prompt": interpolated_prompt,
    }
