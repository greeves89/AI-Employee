"""Command Policies API — DB-backed command filtering rules manageable via UI."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_auth
from app.models.command_policy import CommandPolicy

router = APIRouter(prefix="/command-policies", tags=["command-policies"])


class CreatePolicy(BaseModel):
    name: str
    pattern: str
    effect: str = "blocked"
    scope: str = "global"
    agent_id: str | None = None
    description: str = ""
    is_active: bool = True
    sort_order: int = 100


class UpdatePolicy(BaseModel):
    name: str | None = None
    pattern: str | None = None
    effect: str | None = None
    scope: str | None = None
    agent_id: str | None = None
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


def _to_response(p: CommandPolicy) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "pattern": p.pattern,
        "effect": p.effect,
        "scope": p.scope,
        "agent_id": p.agent_id,
        "description": p.description,
        "is_active": p.is_active,
        "sort_order": p.sort_order,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/")
async def list_policies(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CommandPolicy).order_by(CommandPolicy.sort_order, CommandPolicy.id)
    )
    policies = result.scalars().all()
    return {"policies": [_to_response(p) for p in policies]}


@router.post("/", status_code=201)
async def create_policy(
    body: CreatePolicy,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    if body.effect not in ("blocked", "high", "medium", "allow"):
        raise HTTPException(status_code=400, detail="effect must be blocked, high, medium, or allow")
    if body.scope not in ("global", "agent"):
        raise HTTPException(status_code=400, detail="scope must be global or agent")
    if body.scope == "agent" and not body.agent_id:
        raise HTTPException(status_code=400, detail="agent_id required when scope is agent")

    policy = CommandPolicy(
        name=body.name,
        pattern=body.pattern,
        effect=body.effect,
        scope=body.scope,
        agent_id=body.agent_id if body.scope == "agent" else None,
        description=body.description,
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    db.add(AuditLog(
        agent_id=body.agent_id or "global",
        event_type=AuditEventType.APPROVAL_RULE_CREATED,
        command=f"command_policy: {body.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"policy_id": policy.id, "effect": body.effect, "pattern": body.pattern},
    ))
    await db.commit()
    return _to_response(policy)


@router.patch("/{policy_id}")
async def update_policy(
    policy_id: int,
    body: UpdatePolicy,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    policy = await db.scalar(select(CommandPolicy).where(CommandPolicy.id == policy_id))
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    changes = body.model_dump(exclude_unset=True)
    if "effect" in changes and changes["effect"] not in ("blocked", "high", "medium", "allow"):
        raise HTTPException(status_code=400, detail="effect must be blocked, high, medium, or allow")
    for field, value in changes.items():
        setattr(policy, field, value)
    await db.commit()
    await db.refresh(policy)
    db.add(AuditLog(
        agent_id=policy.agent_id or "global",
        event_type=AuditEventType.APPROVAL_RULE_UPDATED,
        command=f"command_policy: {policy.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"policy_id": policy_id, "changes": changes},
    ))
    await db.commit()
    return _to_response(policy)


@router.delete("/{policy_id}")
async def delete_policy(
    policy_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from app.models.audit_log import AuditLog, AuditEventType
    policy = await db.scalar(select(CommandPolicy).where(CommandPolicy.id == policy_id))
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    name = policy.name
    agent = policy.agent_id or "global"
    await db.delete(policy)
    await db.commit()
    db.add(AuditLog(
        agent_id=agent,
        event_type=AuditEventType.APPROVAL_RULE_DELETED,
        command=f"command_policy: {name}",
        outcome="success",
        user_id=str(user.id),
        meta={"policy_id": policy_id},
    ))
    await db.commit()
    return {"status": "deleted"}


@router.get("/for-agent/{agent_id}")
async def get_policies_for_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Public endpoint — agents fetch their merged policy set (global + agent-specific)."""
    result = await db.execute(
        select(CommandPolicy)
        .where(CommandPolicy.is_active == True)
        .where(or_(
            CommandPolicy.scope == "global",
            (CommandPolicy.scope == "agent") & (CommandPolicy.agent_id == agent_id),
        ))
        .order_by(CommandPolicy.sort_order, CommandPolicy.id)
    )
    policies = result.scalars().all()
    return {
        "policies": [
            {
                "id": p.id,
                "name": p.name,
                "pattern": p.pattern,
                "effect": p.effect,
                "scope": p.scope,
                "description": p.description,
                "sort_order": p.sort_order,
            }
            for p in policies
        ]
    }
