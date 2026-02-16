"""SSO/OIDC service - handles login via Google, Microsoft, etc.

This is separate from OAuthService which handles external integrations.
SSOService handles USER AUTHENTICATION via external identity providers.
"""

import logging
import secrets
import uuid
from urllib.parse import urlencode

import httpx
import jwt as pyjwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.sso_providers import (
    SSOProviderConfig,
    get_sso_client_id,
    get_sso_client_secret,
    get_sso_provider,
)
from app.models.user import User, UserRole
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# CSRF state TTL
SSO_STATE_TTL = 600  # 10 minutes


class SSOService:
    def __init__(self, db: AsyncSession, redis: RedisService):
        self.db = db
        self.redis = redis
        # Cache for JWKS keys
        self._jwks_cache: dict[str, dict] = {}

    async def generate_login_url(self, provider_name: str) -> str:
        """Generate OIDC authorization URL for user login."""
        provider = get_sso_provider(provider_name)
        client_id = get_sso_client_id(provider)
        if not client_id:
            raise ValueError(f"SSO not configured for {provider_name}")

        # Generate and store CSRF state
        state = secrets.token_urlsafe(32)
        state_key = f"sso:state:{state}"
        await self.redis.client.setex(state_key, SSO_STATE_TTL, provider_name)

        redirect_uri = f"{settings.oauth_redirect_base_url}/api/v1/auth/sso/{provider_name}/callback"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(provider.scopes),
            "state": state,
            **provider.auth_extra_params,
        }

        return f"{provider.authorization_url}?{urlencode(params)}"

    async def handle_callback(
        self, provider_name: str, code: str, state: str
    ) -> User:
        """Handle OIDC callback: verify state, exchange code, find/create user."""
        # Verify CSRF state
        state_key = f"sso:state:{state}"
        stored_provider = await self.redis.client.get(state_key)
        if not stored_provider:
            raise ValueError("Invalid or expired SSO state")
        if isinstance(stored_provider, bytes):
            stored_provider = stored_provider.decode()
        if stored_provider != provider_name:
            raise ValueError("SSO state mismatch")
        await self.redis.client.delete(state_key)

        provider = get_sso_provider(provider_name)

        # Exchange code for tokens
        token_data = await self._exchange_code(provider, code)
        id_token_raw = token_data.get("id_token")
        access_token = token_data.get("access_token")

        # Get user info - prefer userinfo endpoint for reliability
        user_info = await self._get_userinfo(provider, access_token)

        # Also try to decode ID token for the subject identifier
        sub = user_info.get("sub")
        email = user_info.get("email")
        name = user_info.get("name", "")
        email_verified = user_info.get("email_verified", False)

        # For Microsoft, email might be in 'mail' or 'userPrincipalName'
        if not email and provider_name == "microsoft":
            email = user_info.get("mail") or user_info.get("userPrincipalName", "")

        if not email:
            raise ValueError("SSO provider did not return an email address")
        if not sub:
            # Fallback: use email as subject if no sub claim
            sub = email

        # Google always returns verified emails; Microsoft depends on tenant
        # For security, we require email verification for account linking
        if provider_name == "google":
            email_verified = True

        # Find or create user
        user = await self._find_or_create_user(
            provider_name=provider_name,
            subject=sub,
            email=email,
            name=name or email.split("@")[0],
            email_verified=email_verified,
        )

        return user

    async def _exchange_code(
        self, provider: SSOProviderConfig, code: str
    ) -> dict:
        """Exchange authorization code for tokens."""
        client_id = get_sso_client_id(provider)
        client_secret = get_sso_client_secret(provider)
        redirect_uri = f"{settings.oauth_redirect_base_url}/api/v1/auth/sso/{provider.name}/callback"

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                provider.token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )

            if resp.status_code != 200:
                logger.error(f"SSO token exchange failed: {resp.status_code} {resp.text}")
                raise ValueError(f"Token exchange failed: {resp.status_code}")

            return resp.json()

    async def _get_userinfo(
        self, provider: SSOProviderConfig, access_token: str
    ) -> dict:
        """Fetch user info from the provider's userinfo endpoint."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                provider.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if resp.status_code != 200:
                logger.error(f"SSO userinfo failed: {resp.status_code} {resp.text}")
                raise ValueError("Failed to fetch user info from SSO provider")

            return resp.json()

    async def _find_or_create_user(
        self,
        provider_name: str,
        subject: str,
        email: str,
        name: str,
        email_verified: bool,
    ) -> User:
        """Find existing user by SSO identity or email, or create new user."""
        # 1. Try to find by SSO identity (provider + subject)
        user = await self.db.scalar(
            select(User).where(
                User.sso_provider == provider_name,
                User.sso_subject == subject,
            )
        )
        if user:
            if not user.is_active:
                raise ValueError("Account is deactivated")
            return user

        # 2. Try to find by email (account linking)
        user = await self.db.scalar(
            select(User).where(User.email == email)
        )
        if user:
            if not user.is_active:
                raise ValueError("Account is deactivated")
            # Link SSO identity to existing account
            # Only if email is verified by the provider (prevents account takeover)
            if email_verified:
                user.sso_provider = provider_name
                user.sso_subject = subject
                await self.db.commit()
                logger.info(f"SSO linked {provider_name} to existing user {email}")
            else:
                logger.warning(
                    f"SSO email not verified for {email}, skipping account link"
                )
            return user

        # 3. Check if registration is open
        from sqlalchemy import func
        user_count = await self.db.scalar(select(func.count()).select_from(User))
        is_first = user_count == 0

        if not is_first and not settings.registration_open:
            raise ValueError("Registration is closed. Contact an admin for access.")

        # 4. Create new user
        user = User(
            id=uuid.uuid4().hex[:12],
            email=email,
            name=name,
            password_hash=None,  # SSO users don't have a password
            role=UserRole.ADMIN if is_first else UserRole.MEMBER,
            sso_provider=provider_name,
            sso_subject=subject,
        )
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)

        logger.info(
            f"SSO user created: {email} via {provider_name} "
            f"(role: {user.role.value}, first: {is_first})"
        )
        return user
