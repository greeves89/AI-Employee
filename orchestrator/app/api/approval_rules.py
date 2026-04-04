"""Approval Rules API — user-defined rules that tell agents when to request approval."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.approval_rule import ApprovalRule

router = APIRouter(prefix="/approval-rules", tags=["approval-rules"])


class CreateRule(BaseModel):
    name: str
    description: str
    category: str = "custom"
    threshold: float | None = None
    agent_id: str | None = None


class UpdateRule(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    threshold: float | None = None
    is_active: bool | None = None
    agent_id: str | None = None


def _to_response(r: ApprovalRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "category": r.category,
        "threshold": r.threshold,
        "is_active": r.is_active,
        "agent_id": r.agent_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/")
async def list_rules(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ApprovalRule).order_by(ApprovalRule.category, ApprovalRule.id)
    )
    rules = result.scalars().all()
    return {"rules": [_to_response(r) for r in rules]}


@router.post("/", status_code=201)
async def create_rule(
    body: CreateRule,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rule = ApprovalRule(
        name=body.name,
        description=body.description,
        category=body.category,
        threshold=body.threshold,
        agent_id=body.agent_id,
        created_by=user.id if user.id != "__anonymous__" else None,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return _to_response(rule)


@router.patch("/{rule_id}")
async def update_rule(
    rule_id: int,
    body: UpdateRule,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rule = await db.scalar(select(ApprovalRule).where(ApprovalRule.id == rule_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return _to_response(rule)


@router.delete("/{rule_id}")
async def delete_rule(
    rule_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    rule = await db.scalar(select(ApprovalRule).where(ApprovalRule.id == rule_id))
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db.delete(rule)
    await db.commit()
    return {"status": "deleted"}


async def get_active_rules_for_agent(db: AsyncSession, agent_id: str) -> list[ApprovalRule]:
    """Return all active rules that apply to a given agent (global rules + agent-specific)."""
    result = await db.execute(
        select(ApprovalRule)
        .where(ApprovalRule.is_active == True)
        .where((ApprovalRule.agent_id.is_(None)) | (ApprovalRule.agent_id == agent_id))
    )
    return list(result.scalars().all())


@router.get("/for-agent/{agent_id}")
async def get_rules_for_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — agents fetch their own applicable rules without auth required.

    Returns rules where agent_id matches OR is NULL (global rules).
    """
    rules = await get_active_rules_for_agent(db, agent_id)
    return {
        "rules": [
            {
                "name": r.name,
                "description": r.description,
                "category": r.category,
                "threshold": r.threshold,
            }
            for r in rules
        ]
    }
