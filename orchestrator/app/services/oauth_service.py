"""OAuth service - handles OAuth flows, token storage, and token refresh."""

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.encryption import decrypt_token, encrypt_token
from app.core.oauth_providers import (
    PROVIDERS,
    get_provider,
    get_provider_client_id,
    get_provider_client_secret,
    is_provider_available,
)
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# State TTL for CSRF protection
STATE_TTL_SECONDS = 600  # 10 minutes


class OAuthService:
    def __init__(self, db: AsyncSession, redis: RedisService):
        self.db = db
        self.redis = redis

    async def generate_auth_url(self, provider_name: str) -> str:
        """Generate OAuth authorization URL with CSRF state."""
        provider = get_provider(provider_name)
        client_id = get_provider_client_id(provider)
        if not client_id:
            raise ValueError(f"No client credentials configured for {provider_name}")

        # Generate and store CSRF state in Redis
        state = secrets.token_urlsafe(32)
        state_key = f"oauth:state:{state}"
        await self.redis.client.setex(state_key, STATE_TTL_SECONDS, provider_name)

        redirect_uri = f"{settings.oauth_redirect_base_url}/api/v1/integrations/{provider_name}/callback"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(provider.scopes),
            "state": state,
            **provider.auth_extra_params,
        }

        return f"{provider.authorization_url}?{urlencode(params)}"

    async def exchange_code(self, provider_name: str, code: str, state: str) -> OAuthIntegration:
        """Exchange authorization code for tokens, encrypt and store them."""
        # Verify CSRF state
        state_key = f"oauth:state:{state}"
        stored_provider = await self.redis.client.get(state_key)
        if not stored_provider:
            raise ValueError("Invalid or expired OAuth state")
        if isinstance(stored_provider, bytes):
            stored_provider = stored_provider.decode()
        if stored_provider != provider_name:
            raise ValueError("OAuth state mismatch")
        await self.redis.client.delete(state_key)

        provider = get_provider(provider_name)
        client_id = get_provider_client_id(provider)
        client_secret = get_provider_client_secret(provider)
        redirect_uri = f"{settings.oauth_redirect_base_url}/api/v1/integrations/{provider_name}/callback"

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                provider.token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
            if token_response.status_code != 200:
                logger.error(f"Token exchange failed: {token_response.text}")
                raise ValueError(f"Token exchange failed: {token_response.status_code}")
            token_data = token_response.json()

        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")
        token_type = token_data.get("token_type", "Bearer")
        scope = token_data.get("scope", " ".join(provider.scopes))

        expires_at = None
        if expires_in:
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

        # Fetch user info for account label
        account_label = None
        if provider.userinfo_url and access_token:
            try:
                async with httpx.AsyncClient() as client:
                    userinfo_resp = await client.get(
                        provider.userinfo_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if userinfo_resp.status_code == 200:
                        userinfo = userinfo_resp.json()
                        account_label = userinfo.get("email") or userinfo.get("mail") or userinfo.get("userPrincipalName")
            except Exception as e:
                logger.warning(f"Could not fetch user info for {provider_name}: {e}")

        # Upsert integration
        provider_enum = OAuthProvider(provider_name)
        result = await self.db.execute(
            select(OAuthIntegration).where(OAuthIntegration.provider == provider_enum)
        )
        integration = result.scalar_one_or_none()

        if integration:
            integration.access_token_encrypted = encrypt_token(access_token)
            integration.refresh_token_encrypted = encrypt_token(refresh_token) if refresh_token else None
            integration.token_type = token_type
            integration.expires_at = expires_at
            integration.scopes = scope
            integration.account_label = account_label or integration.account_label
        else:
            integration = OAuthIntegration(
                provider=provider_enum,
                access_token_encrypted=encrypt_token(access_token),
                refresh_token_encrypted=encrypt_token(refresh_token) if refresh_token else None,
                token_type=token_type,
                expires_at=expires_at,
                scopes=scope,
                account_label=account_label,
            )
            self.db.add(integration)

        await self.db.commit()
        await self.db.refresh(integration)
        return integration

    async def get_valid_token(self, provider_name: str) -> str:
        """Get a valid access token, auto-refreshing if expired."""
        provider_enum = OAuthProvider(provider_name)
        result = await self.db.execute(
            select(OAuthIntegration).where(OAuthIntegration.provider == provider_enum)
        )
        integration = result.scalar_one_or_none()
        if not integration:
            raise ValueError(f"No integration found for {provider_name}")

        # Check if token is expired or about to expire (5 min buffer)
        if integration.expires_at:
            if integration.expires_at < datetime.now(timezone.utc) + timedelta(minutes=5):
                if integration.refresh_token_encrypted:
                    integration = await self._refresh_token(integration)
                else:
                    raise ValueError(f"Token expired for {provider_name} and no refresh token available")

        return decrypt_token(integration.access_token_encrypted)

    async def _refresh_token(self, integration: OAuthIntegration) -> OAuthIntegration:
        """Refresh an expired access token."""
        provider = get_provider(integration.provider.value)
        client_id = get_provider_client_id(provider)
        client_secret = get_provider_client_secret(provider)

        refresh_token = decrypt_token(integration.refresh_token_encrypted)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                provider.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                logger.error(f"Token refresh failed for {integration.provider.value}: {response.text}")
                raise ValueError(f"Token refresh failed: {response.status_code}")
            token_data = response.json()

        integration.access_token_encrypted = encrypt_token(token_data["access_token"])
        if token_data.get("refresh_token"):
            integration.refresh_token_encrypted = encrypt_token(token_data["refresh_token"])
        if token_data.get("expires_in"):
            integration.expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(token_data["expires_in"]))

        await self.db.commit()
        await self.db.refresh(integration)
        logger.info(f"Refreshed token for {integration.provider.value}")
        return integration

    async def disconnect(self, provider_name: str) -> None:
        """Remove an OAuth integration."""
        provider_enum = OAuthProvider(provider_name)
        result = await self.db.execute(
            select(OAuthIntegration).where(OAuthIntegration.provider == provider_enum)
        )
        integration = result.scalar_one_or_none()
        if integration:
            await self.db.delete(integration)
            await self.db.commit()

    async def list_integrations(self) -> list[dict]:
        """List all providers with their connection status."""
        result = await self.db.execute(select(OAuthIntegration))
        connected = {i.provider.value: i for i in result.scalars().all()}

        integrations = []
        for name, provider in PROVIDERS.items():
            integration = connected.get(name)
            integrations.append({
                "provider": name,
                "display_name": provider.display_name,
                "icon": provider.icon,
                "description": provider.description,
                "connected": integration is not None,
                "account_label": integration.account_label if integration else None,
                "expires_at": integration.expires_at.isoformat() if integration and integration.expires_at else None,
                "scopes": integration.scopes if integration else " ".join(provider.scopes),
                "available": is_provider_available(provider),
            })
        return integrations

    async def get_tokens_for_agent(self, agent_integrations: list[str]) -> dict[str, str]:
        """Get decrypted tokens for a list of provider names."""
        tokens = {}
        for provider_name in agent_integrations:
            try:
                token = await self.get_valid_token(provider_name)
                tokens[provider_name] = token
            except (ValueError, Exception) as e:
                logger.warning(f"Could not get token for {provider_name}: {e}")
        return tokens

    async def refresh_expiring_tokens(self) -> None:
        """Background task: refresh tokens that will expire within 10 minutes."""
        threshold = datetime.now(timezone.utc) + timedelta(minutes=10)
        result = await self.db.execute(
            select(OAuthIntegration).where(
                OAuthIntegration.expires_at < threshold,
                OAuthIntegration.refresh_token_encrypted.isnot(None),
            )
        )
        for integration in result.scalars().all():
            try:
                await self._refresh_token(integration)
            except Exception as e:
                logger.error(f"Failed to refresh token for {integration.provider.value}: {e}")
