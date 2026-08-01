"""Admin API for the DLP egress filter (#388).

Toggle the filter, manage per-class (optionally per-agent) rules, preview a scan,
and review recent DLP audit events. Admin-only. The enforcement itself lives in
``app.core.dlp`` and is called from the outbound message paths.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dlp import CLASSES, VALID_ACTIONS, classify
from app.db.session import get_db
from app.dependencies import require_admin
from app.models.audit_log import AuditLog
from app.models.dlp_rule import DlpRule
from app.services.settings_service import SettingsService

router = APIRouter(prefix="/dlp", tags=["dlp"])

_DLP_EVENTS = ("dlp_blocked", "dlp_masked", "dlp_flagged")


class DlpSettingsUpdate(BaseModel):
    enabled: bool


class DlpRuleUpsert(BaseModel):
    pii_class: str
    action: str
    agent_id: str | None = None
    enabled: bool = True


@router.get("/settings")
async def get_settings(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    enabled = (await SettingsService(db).get("dlp_enabled")) in ("true", "1", True)
    return {"enabled": enabled, "classes": list(CLASSES), "actions": sorted(VALID_ACTIONS)}


@router.patch("/settings")
async def update_settings(
    body: DlpSettingsUpdate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    await SettingsService(db).set("dlp_enabled", "true" if body.enabled else "false")
    return {"enabled": body.enabled}


@router.get("/rules")
async def list_rules(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(DlpRule).order_by(DlpRule.pii_class, DlpRule.agent_id.nullsfirst()))).scalars().all()
    return {
        "rules": [
            {
                "id": r.id,
                "pii_class": r.pii_class,
                "agent_id": r.agent_id,
                "action": r.action,
                "enabled": r.enabled,
            }
            for r in rows
        ]
    }


@router.post("/rules")
async def upsert_rule(
    body: DlpRuleUpsert, user=Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    if body.pii_class not in CLASSES:
        raise HTTPException(status_code=400, detail=f"Unknown pii_class (allowed: {', '.join(CLASSES)})")
    if body.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid action (allowed: {', '.join(sorted(VALID_ACTIONS))})")
    # One rule per (class, agent scope): update in place if it exists.
    existing = (await db.execute(
        select(DlpRule).where(DlpRule.pii_class == body.pii_class, DlpRule.agent_id == body.agent_id)
    )).scalar_one_or_none()
    if existing:
        existing.action = body.action
        existing.enabled = body.enabled
        rule = existing
    else:
        rule = DlpRule(
            pii_class=body.pii_class, agent_id=body.agent_id,
            action=body.action, enabled=body.enabled,
        )
        db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return {"id": rule.id, "pii_class": rule.pii_class, "agent_id": rule.agent_id, "action": rule.action, "enabled": rule.enabled}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rule = (await db.execute(select(DlpRule).where(DlpRule.id == rule_id))).scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    return {"deleted": rule_id}


@router.get("/audit")
async def list_audit(
    limit: int = Query(100, ge=1, le=500),
    user=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.event_type.in_(_DLP_EVENTS))
        .order_by(AuditLog.created_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "events": [
            {
                "id": a.id,
                "agent_id": a.agent_id,
                "event_type": a.event_type,
                "channel": a.command,
                "outcome": a.outcome,
                "meta": a.meta,   # matched classes + actions only, never the value
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]
    }


class DlpTestRequest(BaseModel):
    text: str


@router.post("/test")
async def test_scan(body: DlpTestRequest, user=Depends(require_admin)):
    """Preview which classes a sample text would match (no side effects)."""
    return {"classes": classify(body.text)}
