"""API endpoints for OAuth integrations and PAT-based integrations (GitHub)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_admin, require_auth, verify_agent_token
from app.schemas.integration import (
    AgentIntegrationsResponse,
    AgentIntegrationsUpdate,
    AuthUrlResponse,
    IntegrationListResponse,
    IntegrationStatus,
)
from app.services.oauth_service import OAuthService
from app.services.redis_service import RedisService

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _get_oauth_service(
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
) -> OAuthService:
    return OAuthService(db, redis)


@router.get("/", response_model=IntegrationListResponse)
async def list_integrations(user=Depends(require_auth), service: OAuthService = Depends(_get_oauth_service)):
    """List OAuth providers with connection status. Non-admins do not see shared
    (admin-connected, global) integrations unless released — default-deny."""
    from app.core.ownership import is_admin
    integrations = await service.list_integrations(
        user_id=user.id, include_shared=is_admin(user)
    )
    return IntegrationListResponse(
        integrations=[IntegrationStatus(**i) for i in integrations]
    )


@router.get("/{provider}/auth", response_model=AuthUrlResponse)
async def get_auth_url(
    provider: str,
    user=Depends(require_auth),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Generate OAuth authorization URL for a provider."""
    try:
        auth_url = await service.generate_auth_url(provider, user_id=user.id)
        return AuthUrlResponse(auth_url=auth_url, provider=provider)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    service: OAuthService = Depends(_get_oauth_service),
):
    """OAuth callback endpoint - exchanges code for tokens and redirects to frontend."""
    if error:
        # Redirect to frontend with error
        return RedirectResponse(
            url=f"/integrations?error={error}&provider={provider}"
        )

    try:
        await service.exchange_code(provider, code, state)
        # Redirect to frontend with success
        return RedirectResponse(url=f"/integrations?connected={provider}")
    except ValueError as e:
        return RedirectResponse(
            url=f"/integrations?error={str(e)}&provider={provider}"
        )


@router.delete("/{provider}")
async def disconnect_integration(
    provider: str,
    user=Depends(require_auth),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Disconnect an OAuth integration."""
    from app.models.oauth_integration import PER_USER_PROVIDERS
    try:
        uid = user.id if provider in PER_USER_PROVIDERS else None
        await service.disconnect(provider, user_id=uid)
        return {"status": "disconnected", "provider": provider}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{provider}/refresh")
async def refresh_token(
    provider: str,
    user=Depends(require_auth),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Manually refresh an OAuth token."""
    from app.models.oauth_integration import PER_USER_PROVIDERS
    try:
        uid = user.id if provider in PER_USER_PROVIDERS else None
        await service.get_valid_token(provider, user_id=uid)
        return {"status": "refreshed", "provider": provider}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{provider}/token")
async def get_token(
    provider: str,
    user=Depends(require_admin),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Get a fresh decrypted token (admin only — global token only)."""
    try:
        token = await service.get_valid_token(provider)
        return {"token": token, "provider": provider}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{provider}/for-agent")
async def get_token_for_agent(
    provider: str,
    agent_info=Depends(verify_agent_token),
    service: OAuthService = Depends(_get_oauth_service),
    db: AsyncSession = Depends(get_db),
):
    """Get a fresh OAuth token for an authenticated agent (HMAC auth).

    For per-user providers (microsoft, google), looks up the agent's owner user_id
    and returns that user's token. For global providers (github), returns the global token.
    """
    from app.models.agent import Agent
    from app.models.oauth_integration import PER_USER_PROVIDERS
    from sqlalchemy import select

    user_id: str | None = None
    if provider in PER_USER_PROVIDERS:
        agent = await db.scalar(select(Agent).where(Agent.id == agent_info["agent_id"]))
        if agent:
            user_id = agent.user_id

    try:
        token = await service.get_valid_token(provider, user_id=user_id)
        return {"token": token, "provider": provider}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=f"No {provider} integration connected: {e}")


# --- Manual code exchange (Anthropic OAuth — user copies code from platform page) ---


class CodeExchangeRequest(BaseModel):
    code: str
    state: str


@router.post("/{provider}/exchange-code")
async def exchange_code_manual(
    provider: str,
    body: CodeExchangeRequest,
    user=Depends(require_auth),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Exchange an authorization code for tokens (manual flow - user pastes code)."""
    try:
        # Strip URL fragment (everything after #) — user may accidentally copy it
        code = body.code.split("#")[0].strip()
        # exchange_code reads user_id from the state payload stored in Redis
        integration = await service.exchange_code(provider, code, body.state)
        return {
            "status": "connected",
            "provider": provider,
            "account_label": integration.account_label,
            "expires_at": integration.expires_at.isoformat() if integration.expires_at else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- PAT-based integrations (GitHub etc.) ---


class PatRequest(BaseModel):
    token: str


@router.post("/{provider}/pat")
async def store_pat(
    provider: str,
    body: PatRequest,
    user=Depends(require_auth),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Store a Personal Access Token for a provider (e.g., GitHub PAT)."""
    try:
        integration = await service.store_pat(provider, body.token)
        return {
            "status": "connected",
            "provider": provider,
            "account_label": integration.account_label,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- CLI auth.json integrations (Codex ChatGPT Login) ---


class AuthJsonRequest(BaseModel):
    auth_json: str


class CodexDeviceStartResponse(BaseModel):
    session_id: str
    verification_uri: str
    user_code: str
    expires_at: str
    status: str


class CodexDeviceStatusResponse(BaseModel):
    session_id: str
    status: str
    expires_at: str
    verification_uri: str | None = None
    user_code: str | None = None
    account_label: str | None = None
    error: str | None = None


@router.post("/{provider}/auth-json")
async def store_auth_json(
    provider: str,
    body: AuthJsonRequest,
    user=Depends(require_admin),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Store a CLI auth.json blob for providers such as Codex.

    Admin only: this grants agent containers access to the platform-level
    ChatGPT/Codex session.
    """
    try:
        integration = await service.store_auth_json(provider, body.auth_json)
        if provider == "codex":
            from app.services.codex_auth_service import CodexAuthService
            await CodexAuthService().sync_auth_json()
        return {
            "status": "connected",
            "provider": provider,
            "account_label": integration.account_label,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{provider}/device-auth/start", response_model=CodexDeviceStartResponse)
async def start_device_auth(
    provider: str,
    user=Depends(require_admin),
):
    """Start a CLI-backed device-code login flow.

    Codex currently exposes ChatGPT sign-in through its CLI. The orchestrator
    runs `codex login --device-auth`, returns the verification URL/code, then
    stores the generated auth.json encrypted after the browser authorization.
    """
    if provider != "codex":
        raise HTTPException(status_code=400, detail="Device auth is only supported for codex")

    try:
        from app.services.codex_device_auth_service import codex_device_auth_service
        session = await codex_device_auth_service.start()
        return CodexDeviceStartResponse(
            session_id=session.id,
            verification_uri=session.verification_uri,
            user_code=session.code,
            expires_at=session.expires_at.isoformat(),
            status=session.status,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{provider}/device-auth/{session_id}", response_model=CodexDeviceStatusResponse)
async def get_device_auth_status(
    provider: str,
    session_id: str,
    user=Depends(require_admin),
):
    if provider != "codex":
        raise HTTPException(status_code=400, detail="Device auth is only supported for codex")

    from app.services.codex_device_auth_service import codex_device_auth_service
    session = await codex_device_auth_service.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Device auth session not found")

    return CodexDeviceStatusResponse(
        session_id=session.id,
        status=session.status,
        expires_at=session.expires_at.isoformat(),
        verification_uri=session.verification_uri if session.status == "pending" else None,
        user_code=session.code if session.status == "pending" else None,
        account_label=session.account_label,
        error=session.error,
    )


@router.delete("/{provider}/device-auth/{session_id}")
async def cancel_device_auth(
    provider: str,
    session_id: str,
    user=Depends(require_admin),
):
    if provider != "codex":
        raise HTTPException(status_code=400, detail="Device auth is only supported for codex")

    from app.services.codex_device_auth_service import codex_device_auth_service
    cancelled = await codex_device_auth_service.cancel(session_id)
    return {"status": "cancelled" if cancelled else "not_found", "provider": provider}
