"""Audit log API - query and record privileged command executions."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth, verify_agent_token
from app.models.audit_log import AuditLog, AuditEventType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class AuditLogCreate(BaseModel):
    """Agent reports a command execution for audit."""
    event_type: str  # AuditEventType value
    command: Optional[str] = None
    outcome: str = "success"   # success, failure, blocked
    exit_code: Optional[int] = None
    task_id: Optional[str] = None
    approval_id: Optional[str] = None
    meta: Optional[dict] = None


class AuditLogResponse(BaseModel):
    id: int
    agent_id: str
    task_id: Optional[str]
    approval_id: Optional[str]
    event_type: str
    command: Optional[str]
    outcome: str
    exit_code: Optional[int]
    user_id: Optional[str]
    meta: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


# ══════════════════════════════════════════════════════════════════════════════
# AGENT ENDPOINTS (Write)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/log", status_code=201)
async def create_audit_log(
    body: AuditLogCreate,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """
    Agent reports a privileged command execution.

    Called automatically after executing any sudo/privileged command,
    approved tool calls, or blocked attempts.
    """
    agent_id = agent_auth["agent_id"]

    # Validate event_type
    valid_types = {e.value for e in AuditEventType}
    if body.event_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event_type. Must be one of: {sorted(valid_types)}",
        )

    entry = AuditLog(
        agent_id=agent_id,
        task_id=body.task_id,
        approval_id=body.approval_id,
        event_type=body.event_type,
        command=body.command,
        outcome=body.outcome,
        exit_code=body.exit_code,
        meta=body.meta,
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    logger.info(
        f"Audit: agent={agent_id} event={body.event_type} outcome={body.outcome} "
        f"cmd={body.command!r:.80}"
    )

    return {"id": entry.id, "status": "logged"}


# ══════════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS (Read)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/logs")
async def list_audit_logs(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    task_id: Optional[str] = Query(None, description="Filter by task ID"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    outcome: Optional[str] = Query(None, description="Filter by outcome (success/failure/blocked)"),
    since: Optional[datetime] = Query(None, description="Return entries after this timestamp"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Query audit log entries. Returns {logs: [...], total: N} for pagination.
    """
    base = select(AuditLog)

    if agent_id:
        base = base.where(AuditLog.agent_id == agent_id)
    if task_id:
        base = base.where(AuditLog.task_id == task_id)
    if event_type:
        base = base.where(AuditLog.event_type == event_type)
    if outcome:
        base = base.where(AuditLog.outcome == outcome)
    if since:
        base = base.where(AuditLog.created_at >= since)

    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    result = await db.execute(base.order_by(desc(AuditLog.created_at)).offset(offset).limit(limit))
    logs = result.scalars().all()

    return {
        "logs": [
            {
                "id": e.id,
                "agent_id": e.agent_id,
                "task_id": e.task_id,
                "event_type": e.event_type,
                "outcome": e.outcome,
                "command": e.command,
                "details": e.details,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in logs
        ],
        "total": total or 0,
    }


@router.get("/logs/summary")
async def audit_summary(
    agent_id: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return aggregate counts grouped by event_type and outcome.
    Useful for dashboards and compliance reporting.
    """
    stmt = (
        select(
            AuditLog.event_type,
            AuditLog.outcome,
            func.count(AuditLog.id).label("count"),
        )
        .group_by(AuditLog.event_type, AuditLog.outcome)
        .order_by(AuditLog.event_type, AuditLog.outcome)
    )

    if agent_id:
        stmt = stmt.where(AuditLog.agent_id == agent_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)

    result = await db.execute(stmt)
    rows = result.all()

    # Aggregate into frontend-friendly shape
    total = sum(r.count for r in rows)
    by_outcome: dict[str, int] = {"success": 0, "failure": 0, "blocked": 0}
    by_event_type: dict[str, int] = {}
    for r in rows:
        if r.outcome and r.outcome in by_outcome:
            by_outcome[r.outcome] += r.count
        et = r.event_type or "UNKNOWN"
        by_event_type[et] = by_event_type.get(et, 0) + r.count

    return {
        "total": total,
        "by_outcome": by_outcome,
        "by_event_type": by_event_type,
        "detail": [
            {"event_type": r.event_type, "outcome": r.outcome, "count": r.count}
            for r in rows
        ],
    }


@router.get("/logs/{log_id}", response_model=AuditLogResponse)
async def get_audit_log(
    log_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single audit log entry by ID."""
    entry = await db.get(AuditLog, log_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Audit log entry not found")
    return entry
