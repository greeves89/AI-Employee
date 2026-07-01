"""Regression test for issue #250: OAuth re-auth 500 — UniqueViolation bei persist_tokens.

Root cause: get_auth_url passes user.id for ALL providers. For global providers
(Anthropic, GitHub, …), persist_tokens then SELECTs with user_id=<user.id> and
misses the existing NULL-user_id row, falling through to an INSERT that violates
the unique constraint on (provider).

Fix: persist_tokens normalises user_id → None for non-per-user providers.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.oauth_integration import OAuthIntegration, OAuthProvider


def _make_integration(provider: OAuthProvider, user_id=None) -> OAuthIntegration:
    row = OAuthIntegration()
    row.id = 1
    row.provider = provider
    row.user_id = user_id
    row.access_token_encrypted = "enc:old-token"
    row.refresh_token_encrypted = None
    row.token_type = "Bearer"
    row.expires_at = None
    row.scopes = ""
    row.account_label = None
    return row


def _mock_encrypt(token):
    return f"enc:{token}"


class PersistTokensGlobalProviderTests(unittest.IsolatedAsyncioTestCase):
    """persist_tokens must UPDATE (not INSERT) when re-authing a global provider."""

    async def _make_service(self, existing_row):
        from app.services.oauth_service import OAuthService
        from app.services.redis_service import RedisService

        db = AsyncMock()
        scalar_result = MagicMock()
        scalar_result.scalar_one_or_none.return_value = existing_row
        db.execute = AsyncMock(return_value=scalar_result)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        redis = MagicMock(spec=RedisService)
        return OAuthService(db, redis)

    @patch("app.services.oauth_service.encrypt_token", side_effect=_mock_encrypt)
    @patch("app.services.oauth_service.httpx.AsyncClient")
    async def test_reauth_global_provider_updates_existing_row(self, _http, _enc):
        """Re-auth with a non-None user_id must UPDATE the NULL-user_id global row."""
        existing = _make_integration(OAuthProvider.ANTHROPIC, user_id=None)
        service = await self._make_service(existing_row=existing)

        token_data = {"access_token": "new-token", "token_type": "Bearer"}
        result = await service.persist_tokens("anthropic", "user-abc123", token_data)

        # Must have updated the existing row, not tried to create a new one
        assert result is existing, "persist_tokens must return the EXISTING row, not a new one"
        service.db.add.assert_not_called()

    @patch("app.services.oauth_service.encrypt_token", side_effect=_mock_encrypt)
    @patch("app.services.oauth_service.httpx.AsyncClient")
    async def test_reauth_global_provider_no_insert_when_row_exists(self, _http, _enc):
        """db.add must never be called for a global provider that already has a row."""
        existing = _make_integration(OAuthProvider.GITHUB, user_id=None)
        service = await self._make_service(existing_row=existing)

        token_data = {"access_token": "ghp_newtoken", "token_type": "token"}
        await service.persist_tokens("github", "some-user-id", token_data)

        service.db.add.assert_not_called()

    @patch("app.services.oauth_service.encrypt_token", side_effect=_mock_encrypt)
    @patch("app.services.oauth_service.httpx.AsyncClient")
    async def test_first_auth_global_provider_inserts_with_null_user_id(self, _http, _enc):
        """First-time auth for a global provider must INSERT with user_id=NULL."""
        service = await self._make_service(existing_row=None)

        token_data = {"access_token": "first-token", "token_type": "Bearer"}

        inserted = []
        service.db.add = MagicMock(side_effect=inserted.append)

        await service.persist_tokens("anthropic", "user-xyz", token_data)

        assert len(inserted) == 1, "Expected exactly one INSERT"
        assert inserted[0].user_id is None, "Global provider row must have user_id=NULL"

    @patch("app.services.oauth_service.encrypt_token", side_effect=_mock_encrypt)
    @patch("app.services.oauth_service.httpx.AsyncClient")
    async def test_reauth_per_user_provider_preserves_user_id(self, _http, _enc):
        """persist_tokens for a per-user provider (Microsoft) must keep user_id."""
        service = await self._make_service(existing_row=None)

        inserted = []
        service.db.add = MagicMock(side_effect=inserted.append)

        token_data = {"access_token": "ms-token", "token_type": "Bearer"}
        await service.persist_tokens("microsoft", "user-xyz", token_data)

        assert len(inserted) == 1, "Expected exactly one INSERT"
        assert inserted[0].user_id == "user-xyz", "Per-user provider must preserve user_id"


if __name__ == "__main__":
    unittest.main()
