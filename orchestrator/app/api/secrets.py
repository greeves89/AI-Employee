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

from app.core.encryption import decrypt_token, encrypt_token
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.agent_secret import AgentSecret, AgentSecretAssignment, SecretType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secrets", tags=["secrets"])


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
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

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
    return _serialize(secret)


@router.delete("/{secret_id}", status_code=204)
async def delete_secret(
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")
    await db.delete(secret)
    await db.commit()


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
):
    secret = await db.get(AgentSecret, secret_id)
    if not secret:
        raise HTTPException(status_code=404, detail="Secret not found")

    existing = await db.execute(
        select(AgentSecretAssignment).where(
            AgentSecretAssignment.agent_id == agent_id,
            AgentSecretAssignment.secret_id == secret_id,
        )
    )
    if existing.scalar_one_or_none():
        return {"ok": True, "message": "Already assigned"}

    db.add(AgentSecretAssignment(agent_id=agent_id, secret_id=secret_id))
    await db.commit()
    return {"ok": True}


@router.delete("/agent/{agent_id}/{secret_id}", status_code=204)
async def unassign_secret(
    agent_id: str,
    secret_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        delete(AgentSecretAssignment).where(
            AgentSecretAssignment.agent_id == agent_id,
            AgentSecretAssignment.secret_id == secret_id,
        )
    )
    await db.commit()
