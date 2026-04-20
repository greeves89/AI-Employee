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
    """List all OAuth providers with their connection status."""
    integrations = await service.list_integrations()
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
        auth_url = await service.generate_auth_url(provider)
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
    try:
        await service.disconnect(provider)
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
    try:
        token = await service.get_valid_token(provider)
        return {"status": "refreshed", "provider": provider}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{provider}/token")
async def get_token(
    provider: str,
    user=Depends(require_admin),
    service: OAuthService = Depends(_get_oauth_service),
):
    """Get a fresh decrypted token (admin only — for agent internal use)."""
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
):
    """Get a fresh OAuth token for an authenticated agent (HMAC auth).

    Used by MCP servers running inside agent containers to call
    external APIs (Gmail, Outlook, etc.) on behalf of the user.
    """
    try:
        token = await service.get_valid_token(provider)
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
