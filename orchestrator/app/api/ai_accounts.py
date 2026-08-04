"""AI Accounts API — admin-managed, reusable LLM model accounts.

Admins create/edit/delete accounts; any authenticated user may list them so
they can attach one to an agent. API keys are Fernet-encrypted and never
returned in responses.
"""
from datetime import datetime, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token, encrypt_token
from app.db.session import get_db
from app.services.ai_account_discovery import discover_models
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
    last_checked_at: datetime | None
    last_status: str | None
    last_error: str | None
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
        last_checked_at=a.last_checked_at,
        last_status=a.last_status,
        last_error=a.last_error,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


class DiscoverModelsRequest(BaseModel):
    # Either probe an unsaved account (all three fields), or re-check a saved one
    # by id (missing fields fall back to the stored values / decrypted key).
    provider_type: ProviderType | None = None
    api_endpoint: str | None = None
    api_key: str | None = None
    account_id: int | None = None


def _short_error(text: str | None) -> str | None:
    if not text:
        return None
    compact = " ".join(str(text).split())
    return compact[:252] + "..." if len(compact) > 255 else compact


async def _fetch_provider_models(url: str, headers: dict, params: dict) -> tuple[int | None, dict | None]:
    """SSRF-guarded GET used by the discovery service. Raises to signal a blocked
    host (the service maps any exception to ``unreachable``, so no request leaks)."""
    from app.api.mcp_servers import _assert_mcp_url_allowed

    await _assert_mcp_url_allowed(url)  # 400 on private/invalid host; caught upstream
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        resp = await client.get(url, headers=headers, params=params)
    try:
        body = resp.json()
    except Exception:
        body = None
    return resp.status_code, body


@router.get("/", response_model=list[AIAccountResponse])
async def list_ai_accounts(
    active_only: bool = False,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List AI accounts. Admins see all; non-admins see ONLY accounts explicitly
    released to them via their role (custom_role.permissions.ai_account_ids).

    Default-deny: a user without an explicit allowlist entry sees NOTHING — shared
    platform accounts (Claude/Codex/AWS) are not auto-visible to every tenant."""
    stmt = select(AIAccount).order_by(AIAccount.name)
    if active_only:
        stmt = stmt.where(AIAccount.is_active.is_(True))
    rows = (await db.execute(stmt)).scalars().all()

    if not (hasattr(user, "role") and user.role == UserRole.ADMIN):
        from app.core.permissions import get_effective_permissions
        perms = await get_effective_permissions(user, db)
        allowed = perms.get("ai_account_ids")
        allowed_set = set(allowed) if allowed is not None else set()  # None = deny, not "all"
        rows = [a for a in rows if a.id in allowed_set]
    return [_to_response(a) for a in rows]


async def _allowed_account_ids(user, db: AsyncSession) -> set[int] | None:
    """AI-account ids the user may use; None = unrestricted (admins only). Mirrors the
    default-deny permission model of list_ai_accounts (custom_role.ai_account_ids
    allowlist). A non-admin without an explicit allowlist gets an EMPTY set (deny),
    never unrestricted access to shared platform accounts."""
    if hasattr(user, "role") and user.role == UserRole.ADMIN:
        return None
    from app.core.permissions import get_effective_permissions
    perms = await get_effective_permissions(user, db)
    allowed = perms.get("ai_account_ids")
    return set(allowed) if allowed is not None else set()


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
    allowed_ids = await _allowed_account_ids(user, db)
    out: list[dict] = []
    for a in rows:
        if allowed_ids is not None and a.id not in allowed_ids:
            continue
        prov = REALTIME_PROVIDERS.get(a.provider_type)
        if not prov:
            continue
        # Prefer the models the admin actually configured on THIS account (so the
        # selector shows exactly what exists — e.g. one gpt-realtime — instead of a
        # hardwired catalog list that would list several identical-engine options and
        # all light up as "active" together). Fall back to the catalog if empty.
        acct_models = [
            {"id": m["name"], "label": m.get("label") or m["name"]}
            for m in (a.models or []) if isinstance(m, dict) and m.get("name")
        ]
        for m in (acct_models or prov["models"]):
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


@router.post("/discover-models")
async def discover_ai_account_models(
    body: DiscoverModelsRequest,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Discover the models a provider actually exposes, and record connection health.

    Mirrors ``POST /mcp-servers/probe``: validates a target before anything is
    committed. Admin only. If ``account_id`` is given the stored key/endpoint are
    used (no need to re-enter the key) and the account's health columns are
    stamped with the result, so an unusable account becomes visible in the list.
    """
    _require_admin(user)

    provider_type = body.provider_type
    api_endpoint = body.api_endpoint
    api_key = body.api_key
    account: AIAccount | None = None

    if body.account_id is not None:
        account = await db.get(AIAccount, body.account_id)
        if not account:
            raise HTTPException(status_code=404, detail="AI account not found")
        provider_type = provider_type or account.provider_type
        if api_endpoint is None:
            api_endpoint = account.api_endpoint
        if not api_key and account.api_key_encrypted:
            api_key = decrypt_token(account.api_key_encrypted)

    if not provider_type:
        raise HTTPException(status_code=400, detail="provider_type is required")

    result = await discover_models(provider_type, api_endpoint, api_key, _fetch_provider_models)

    if account is not None:
        account.last_checked_at = datetime.now(timezone.utc)
        account.last_status = result["status"]
        account.last_error = _short_error(result.get("error"))
        await db.commit()

    return result


@router.get("/{account_id}", response_model=AIAccountResponse)
async def get_ai_account(
    account_id: int,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    account = await db.get(AIAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="AI account not found")
    # Default-deny: a non-admin may only read an account released to them.
    allowed = await _allowed_account_ids(user, db)
    if allowed is not None and account_id not in allowed:
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
