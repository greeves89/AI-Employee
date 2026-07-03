"""AI Accounts API — admin-managed, reusable LLM model accounts.

Admins create/edit/delete accounts; any authenticated user may list them so
they can attach one to an agent. API keys are Fernet-encrypted and never
returned in responses.
"""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import encrypt_token
from app.db.session import get_db
from app.dependencies import require_auth
from app.models.ai_account import AIAccount
from app.models.user import UserRole

router = APIRouter(prefix="/ai-accounts", tags=["ai-accounts"])

ProviderType = Literal[
    "azure-openai", "openai", "anthropic", "google", "ollama", "lm-studio",
    # Realtime voice fronts + tools (configured here, selected in the voice setup)
    "bedrock", "azure-realtime", "brave-search",
]


def _require_admin(user) -> None:
    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Admin only")


class AIModelEntry(BaseModel):
    name: str
    provider_type: ProviderType
    api_endpoint: str = ""


def _normalize_models(raw: list, default_provider: str, default_endpoint: str | None) -> list[dict]:
    """Accept model entries as objects or legacy plain strings → list of dicts."""
    out: list[dict] = []
    for m in raw or []:
        if isinstance(m, str):
            out.append({"name": m, "provider_type": default_provider,
                        "api_endpoint": default_endpoint or ""})
        elif isinstance(m, dict) and m.get("name"):
            out.append({
                "name": m["name"],
                "provider_type": m.get("provider_type") or default_provider,
                "api_endpoint": m.get("api_endpoint") or default_endpoint or "",
            })
    return out


class AIAccountCreate(BaseModel):
    name: str
    provider_type: ProviderType
    api_endpoint: str | None = None
    api_key: str | None = None  # plaintext on input; stored encrypted
    models: list[AIModelEntry] = []  # each model carries its own surface
    extra: dict = {}


class AIAccountUpdate(BaseModel):
    name: str | None = None
    provider_type: ProviderType | None = None
    api_endpoint: str | None = None
    api_key: str | None = None  # only set to change it
    models: list[AIModelEntry] | None = None
    extra: dict | None = None
    is_active: bool | None = None


class AIAccountResponse(BaseModel):
    id: int
    name: str
    provider_type: str
    api_endpoint: str | None
    models: list[dict]
    extra: dict
    is_active: bool
    has_key: bool
    created_at: datetime
    updated_at: datetime


def _to_response(a: AIAccount) -> AIAccountResponse:
    return AIAccountResponse(
        id=a.id,
        name=a.name,
        provider_type=a.provider_type,
        api_endpoint=a.api_endpoint,
        models=_normalize_models(a.models or [], a.provider_type, a.api_endpoint),
        extra=a.extra or {},
        is_active=a.is_active,
        has_key=bool(a.api_key_encrypted),
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


@router.get("/", response_model=list[AIAccountResponse])
async def list_ai_accounts(
    active_only: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List AI accounts. Admins see all; non-admins see only the accounts their
    group/role allows (custom_role.permissions.ai_account_ids; None = all)."""
    stmt = select(AIAccount).order_by(AIAccount.name)
    if active_only:
        stmt = stmt.where(AIAccount.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()

    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from app.core.permissions import get_effective_permissions
        perms = await get_effective_permissions(user, db)
        allowed = perms.get("ai_account_ids")
        if allowed is not None:
            allowed_set = set(allowed)
            rows = [a for a in rows if a.id in allowed_set]
    return [_to_response(a) for a in rows]


@router.get("/realtime-models")
async def list_realtime_models(
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Realtime voice models available from configured AI-accounts.

    Powers the voice-setup selector: for every active AI-account whose provider is
    a realtime provider (AWS Bedrock, Azure Realtime, …), list the models the user
    can pick. ``value`` = ``"<account_id>:<model_id>"``.
    """
    from app.core.realtime_catalog import REALTIME_PROVIDERS, IMPLEMENTED_ENGINES

    rows = (await db.execute(
        select(AIAccount).where(AIAccount.is_active.is_(True)).order_by(AIAccount.name)
    )).scalars().all()
    out: list[dict] = []
    for a in rows:
        prov = REALTIME_PROVIDERS.get(a.provider_type)
        if not prov:
            continue
        for m in prov["models"]:
            out.append({
                "account_id": a.id,
                "account_name": a.name,
                "provider_type": a.provider_type,
                "provider_label": prov["label"],
                "engine": prov["engine"],
                "implemented": prov["engine"] in IMPLEMENTED_ENGINES,
                "model_id": m["id"],
                "model_label": m["label"],
                "value": f"{a.id}:{m['id']}",
                "label": f"{m['label']} · {a.name}",
            })
    return {"models": out}


@router.get("/{account_id}", response_model=AIAccountResponse)
async def get_ai_account(
    account_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    account = await db.get(AIAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="AI account not found")
    return _to_response(account)


@router.post("/", response_model=AIAccountResponse, status_code=201)
async def create_ai_account(
    body: AIAccountCreate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create an AI account (admin only)."""
    _require_admin(user)
    account = AIAccount(
        name=body.name,
        provider_type=body.provider_type,
        api_endpoint=body.api_endpoint,
        api_key_encrypted=encrypt_token(body.api_key) if body.api_key else None,
        models=[m.model_dump() for m in (body.models or [])],
        extra=body.extra or {},
    )
    db.add(account)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An account with this name already exists")
    await db.refresh(account)
    return _to_response(account)


@router.patch("/{account_id}", response_model=AIAccountResponse)
async def update_ai_account(
    account_id: int,
    body: AIAccountUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update an AI account (admin only). api_key only changes if provided."""
    _require_admin(user)
    account = await db.get(AIAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="AI account not found")

    if body.name is not None:
        account.name = body.name
    if body.provider_type is not None:
        account.provider_type = body.provider_type
    if body.api_endpoint is not None:
        account.api_endpoint = body.api_endpoint
    if body.api_key:
        account.api_key_encrypted = encrypt_token(body.api_key)
    if body.models is not None:
        account.models = [m.model_dump() for m in body.models]
    if body.extra is not None:
        account.extra = body.extra
    if body.is_active is not None:
        account.is_active = body.is_active

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An account with this name already exists")
    await db.refresh(account)
    return _to_response(account)


@router.delete("/{account_id}")
async def delete_ai_account(
    account_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete an AI account (admin only). Agents referencing it keep running;
    their ai_account_id is set to NULL by the DB foreign key."""
    _require_admin(user)
    account = await db.get(AIAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="AI account not found")
    await db.delete(account)
    await db.commit()
    return {"ok": True, "id": account_id}
