"""Key Management System API — encrypted secrets for agents.

Secrets (API keys, SSO profiles, OAuth tokens) are stored Fernet-encrypted.
They are assigned to agents and injected as env vars at task runtime.
The plaintext value is only visible at creation time — reads return masked values.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import AgentManager
from app.core.encryption import decrypt_token, encrypt_token
from app.db.session import get_db
from app.dependencies import get_docker_service, get_redis_service, require_auth
from app.models.agent_secret import AgentSecret, AgentSecretAssignment, SecretType
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secrets", tags=["secrets"])


def _get_agent_manager(
    db: AsyncSession = Depends(get_db),
    docker: DockerService = Depends(get_docker_service),
    redis: RedisService = Depends(get_redis_service),
) -> AgentManager:
    return AgentManager(db, docker, redis)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SecretCreate(BaseModel):
    name: str
    key_name: str
    value: str
    secret_type: SecretType = SecretType.API_KEY
    description: str = ""


class SecretUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    value: str | None = None
    is_active: bool | None = None


def _mask(value_encrypted: str) -> str:
    try:
        plain = decrypt_token(value_encrypted)
        if len(plain) <= 8:
            return "****"
        return plain[:4] + "****" + plain[-4:]
    except Exception:
        return "****"


def _serialize(s: AgentSecret, include_mask: bool = True) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "key_name": s.key_name,
        "secret_type": s.secret_type,
        "description": s.description,
        "is_active": s.is_active,
        "masked_value": _mask(s.value_encrypted) if include_mask else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "assigned_agent_ids": [a.agent_id for a in s.assignments],
    }


async def _refresh_agents_for_secret(
    db: AsyncSession,
    manager: AgentManager,
    secret_id: int,
    agent_ids: list[str] | None = None,
) -> dict:
    if agent_ids is None:
        result = await db.execute(
            select(AgentSecretAssignment.agent_id).where(
                AgentSecretAssignment.secret_id == secret_id
            )
        )
        agent_ids = list(result.scalars().all())

    refreshed: list[str] = []
    warnings: list[str] = []
    for agent_id in sorted(set(agent_ids)):
        try:
            await manager.update_agent(agent_id)
            refreshed.append(agent_id)
        except Exception as exc:
            logger.warning("Could not refresh agent %s after secret change: %s", agent_id, exc)
            warnings.append(f"{agent_id}: {exc}")

    return {"refreshed_agent_ids": refreshed, "warnings": warnings}


# ---------------------------------------------------------------------------
# Secret CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_secrets(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AgentSecret).order_by(AgentSecret.name))
    secrets = result.scalars().all()

    # Non-admins only see the secrets their group/role allows
    # (custom_role.permissions.secret_ids; None = all).
    from app.models.user import UserRole
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from app.core.permissions import get_effective_permissions
        perms = await get_effective_permissions(user, db)
        allowed = perms.get("secret_ids")
        if allowed is not None:
            allowed_set = set(allowed)
            secrets = [s for s in secrets if s.id in allowed_set]
    return {"secrets": [_serialize(s) for s in secrets]}


@router.post("", status_code=201)
async def create_secret(
    body: SecretCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    secret = AgentSecret(
        name=body.name,
        key_name=body.key_name.upper().replace(" ", "_"),
        value_encrypted=encrypt_token(body.value),
        secret_type=body.secret_type,
        description=body.description,
        created_by=getattr(user, "email", None),
    )
    db.add(secret)
    await db.commit()
    await db.refresh(secret)
    return _serialize(secret)


@router.patch("/{secret_id}")
async def update_secret(
    secret_id: int,
    body: SecretUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    should_refresh = body.value is not None or body.is_active is not None
    if body.name is not None:
        secret.name = body.name
    if body.description is not None:
        secret.description = body.description
    if body.is_active is not None:
        secret.is_active = body.is_active
    if body.value is not None:
        secret.value_encrypted = encrypt_token(body.value)

    await db.commit()
    await db.refresh(secret)
    response = _serialize(secret)
    if should_refresh:
        response["refresh"] = await _refresh_agents_for_secret(db, manager, secret_id)
    return response


@router.delete("/{secret_id}", status_code=204)
async def delete_secret(
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    result = await db.execute(
        select(AgentSecretAssignment.agent_id).where(
            AgentSecretAssignment.secret_id == secret_id
        )
    )
    agent_ids = list(result.scalars().all())
    await db.delete(secret)
    await db.commit()
    if agent_ids:
        await _refresh_agents_for_secret(db, manager, secret_id, agent_ids)


# ---------------------------------------------------------------------------
# Agent assignment
# ---------------------------------------------------------------------------

@router.get("/agent/{agent_id}")
async def get_agent_secrets(
    agent_id: str,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentSecretAssignment).where(AgentSecretAssignment.agent_id == agent_id)
    )
    assignments = result.scalars().all()
    secret_ids = [a.secret_id for a in assignments]

    secrets = []
    if secret_ids:
        s_result = await db.execute(select(AgentSecret).where(AgentSecret.id.in_(secret_ids)))
        secrets = s_result.scalars().all()

    return {"agent_id": agent_id, "secrets": [_serialize(s) for s in secrets]}


@router.post("/agent/{agent_id}/{secret_id}", status_code=201)
async def assign_secret(
    agent_id: str,
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    # Non-admins may only assign secrets their group/role allows.
    from app.models.user import UserRole
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from app.core.permissions import get_effective_permissions, can_use_secret
        perms = await get_effective_permissions(user, db)
        if not can_use_secret(perms, secret_id):
            raise HTTPException(
                status_code=403,
                detail="Dieser Key/Secret ist für deine Gruppe nicht freigegeben.",
            )

    existing = await db.execute(
        select(AgentSecretAssignment).where(
            AgentSecretAssignment.agent_id == agent_id,
            AgentSecretAssignment.secret_id == secret_id,
        )
    )
    if existing.scalar_one_or_none():
        refresh = await _refresh_agents_for_secret(db, manager, secret_id, [agent_id])
        return {"ok": True, "message": "Already assigned", "refresh": refresh}

    db.add(AgentSecretAssignment(agent_id=agent_id, secret_id=secret_id))
    await db.commit()
    refresh = await _refresh_agents_for_secret(db, manager, secret_id, [agent_id])
    return {"ok": True, "refresh": refresh}


@router.delete("/agent/{agent_id}/{secret_id}", status_code=204)
async def unassign_secret(
    agent_id: str,
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    manager: AgentManager = Depends(_get_agent_manager),
):
    await db.execute(
        delete(AgentSecretAssignment).where(
            AgentSecretAssignment.agent_id == agent_id,
            AgentSecretAssignment.secret_id == secret_id,
        )
    )
    await db.commit()
    await _refresh_agents_for_secret(db, manager, secret_id, [agent_id])
