"""Command policy API.

Policies are evaluated inside agent containers before bash execution. Global
policies apply to all agents; agent policies can override earlier matches by
using a lower sort order.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import require_agent_access, require_auth, verify_agent_token
from app.models.agent import Agent
from app.models.audit_log import AuditEventType, AuditLog
from app.models.command_policy import CommandPolicy
from app.models.user import UserRole

router = APIRouter(prefix="/command-policies", tags=["command-policies"])

VALID_EFFECTS = {"blocked", "high", "medium", "allow"}
VALID_SCOPES = {"global", "agent"}


class CreatePolicy(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    pattern: str = Field(min_length=1)
    effect: str = "blocked"
    scope: str = "global"
    agent_id: str | None = None
    description: str = ""
    is_active: bool = True
    sort_order: int = 100


class UpdatePolicy(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    pattern: str | None = Field(default=None, min_length=1)
    effect: str | None = None
    scope: str | None = None
    agent_id: str | None = None
    description: str | None = None
    is_active: bool | None = None
    sort_order: int | None = None


def _to_response(policy: CommandPolicy) -> dict:
    return {
        "id": policy.id,
        "name": policy.name,
        "pattern": policy.pattern,
        "effect": policy.effect,
        "scope": policy.scope,
        "agent_id": policy.agent_id,
        "description": policy.description,
        "is_active": policy.is_active,
        "sort_order": policy.sort_order,
        "created_at": policy.created_at.isoformat() if policy.created_at else None,
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }


def _validate_policy_fields(effect: str | None, scope: str | None, agent_id: str | None) -> None:
    if effect is not None and effect not in VALID_EFFECTS:
        raise HTTPException(status_code=400, detail="effect must be one of: allow, medium, high, blocked")
    if scope is not None and scope not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail="scope must be global or agent")
    if scope == "agent" and not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required when scope is agent")


async def _assert_manage_access(user, db: AsyncSession, agent_id: str | None) -> None:
    if user.role in (UserRole.ADMIN, UserRole.MANAGER):
        return
    if not agent_id:
        raise HTTPException(status_code=403, detail="Only admins/managers can manage global command policies")
    await require_agent_access(agent_id, user, db)


@router.get("/")
async def list_policies(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    query = select(CommandPolicy).order_by(CommandPolicy.sort_order, CommandPolicy.id)
    if user.role not in (UserRole.ADMIN, UserRole.MANAGER):
        owned_agents = await db.scalars(select(Agent.id).where(Agent.user_id == user.id))
        agent_ids = list(owned_agents.all())
        query = query.where(or_(CommandPolicy.scope == "global", CommandPolicy.agent_id.in_(agent_ids)))
    result = await db.execute(query)
    return {"policies": [_to_response(policy) for policy in result.scalars().all()]}


@router.post("/", status_code=201)
async def create_policy(
    body: CreatePolicy,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    _validate_policy_fields(body.effect, body.scope, body.agent_id)
    await _assert_manage_access(user, db, body.agent_id if body.scope == "agent" else None)

    policy = CommandPolicy(
        name=body.name.strip(),
        pattern=body.pattern.strip(),
        effect=body.effect,
        scope=body.scope,
        agent_id=body.agent_id if body.scope == "agent" else None,
        description=body.description.strip(),
        is_active=body.is_active,
        sort_order=body.sort_order,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)

    db.add(AuditLog(
        agent_id=policy.agent_id or "global",
        event_type=AuditEventType.APPROVAL_RULE_CREATED,
        command=f"command_policy: {policy.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"policy_id": policy.id, "effect": policy.effect, "pattern": policy.pattern},
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
    policy = await db.scalar(select(CommandPolicy).where(CommandPolicy.id == policy_id))
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")

    changes = body.model_dump(exclude_unset=True)
    next_scope = changes.get("scope", policy.scope)
    next_agent_id = changes.get("agent_id", policy.agent_id)
    _validate_policy_fields(changes.get("effect"), next_scope, next_agent_id)
    await _assert_manage_access(user, db, next_agent_id if next_scope == "agent" else None)

    for field, value in changes.items():
        if field in {"name", "pattern", "description"} and isinstance(value, str):
            value = value.strip()
        setattr(policy, field, value)
    if policy.scope == "global":
        policy.agent_id = None

    await db.commit()
    await db.refresh(policy)

    db.add(AuditLog(
        agent_id=policy.agent_id or "global",
        event_type=AuditEventType.APPROVAL_RULE_UPDATED,
        command=f"command_policy: {policy.name}",
        outcome="success",
        user_id=str(user.id),
        meta={"policy_id": policy.id, "changes": changes},
    ))
    await db.commit()
    return _to_response(policy)


@router.delete("/{policy_id}")
async def delete_policy(
    policy_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    policy = await db.scalar(select(CommandPolicy).where(CommandPolicy.id == policy_id))
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    await _assert_manage_access(user, db, policy.agent_id)

    name = policy.name
    agent_id = policy.agent_id or "global"
    await db.delete(policy)
    await db.commit()

    db.add(AuditLog(
        agent_id=agent_id,
        event_type=AuditEventType.APPROVAL_RULE_DELETED,
        command=f"command_policy: {name}",
        outcome="success",
        user_id=str(user.id),
        meta={"policy_id": policy_id},
    ))
    await db.commit()
    return {"status": "deleted"}


@router.get("/for-agent/{agent_id}")
async def get_policies_for_agent(
    agent_id: str,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Agent-only endpoint returning active global + agent-specific policies."""
    if agent_auth["agent_id"] != agent_id:
        raise HTTPException(status_code=403, detail="Agent token does not match requested agent")

    result = await db.execute(
        select(CommandPolicy)
        .where(CommandPolicy.is_active.is_(True))
        .where(or_(
            CommandPolicy.scope == "global",
            (CommandPolicy.scope == "agent") & (CommandPolicy.agent_id == agent_id),
        ))
        .order_by(CommandPolicy.sort_order, CommandPolicy.id)
    )
    return {
        "policies": [
            {
                "id": policy.id,
                "name": policy.name,
                "pattern": policy.pattern,
                "effect": policy.effect,
                "scope": policy.scope,
                "description": policy.description,
                "sort_order": policy.sort_order,
            }
            for policy in result.scalars().all()
        ]
    }
