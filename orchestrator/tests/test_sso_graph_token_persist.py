"""Regression test for issue #1009: SSO login discarded a fresh Graph access_token
whenever Microsoft didn't also rotate the refresh_token in that exchange.

Root cause: ``SSOService.handle_callback`` only called ``OAuthService.persist_tokens``
when ``token_data`` contained a ``refresh_token``. ``persist_tokens`` already keeps
any EXISTING refresh_token when the response omits one — so gating the whole call
on the refresh_token's presence just threw away a perfectly valid, longer-lived
access_token on logins that didn't rotate it, leaving the OLD access_token to expire
with nothing refreshing it (the "Token expired ... no refresh token available" error
from oauth_service.get_valid_token).

Fix: persist whenever an access_token came back, regardless of refresh_token.
"""

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.user import User, UserRole


def _make_user() -> User:
    user = User()
    user.id = "user-1"
    user.email = "m.mustermann@example.invalid"
    user.role = UserRole.MEMBER
    user.custom_role_id = None
    return user


class SsoGraphTokenPersistTests(unittest.IsolatedAsyncioTestCase):
    async def _make_service(self):
        from app.services.sso_service import SSOService
        from app.services.redis_service import RedisService

        db = AsyncMock()
        redis = MagicMock(spec=RedisService)
        redis.client = AsyncMock()
        redis.client.get = AsyncMock(
            return_value=json.dumps({"provider": "microsoft", "return_to": ""})
        )
        redis.client.delete = AsyncMock()

        service = SSOService(db, redis)
        service._exchange_code = AsyncMock()
        service._get_userinfo = AsyncMock(
            return_value={"sub": "ms-sub-1", "email": "m.mustermann@example.invalid",
                          "name": "M. Mustermann", "email_verified": True}
        )
        service._fetch_microsoft_groups = AsyncMock(return_value=[])
        service.apply_group_role = AsyncMock(return_value=False)
        service._find_or_create_user = AsyncMock(return_value=_make_user())
        return service

    @patch("app.services.oauth_service.OAuthService.persist_tokens", new_callable=AsyncMock)
    async def test_login_without_refresh_token_still_persists_access_token(self, persist_mock):
        """A login whose token response lacks refresh_token must still be stored."""
        service = await self._make_service()
        service._exchange_code.return_value = {
            "access_token": "fresh-access-token",
            "id_token": "ignored",
            "expires_in": 3600,
            # No refresh_token in this response — e.g. Microsoft didn't rotate it.
        }

        await service.handle_callback("microsoft", "code-123", "state-abc")

        persist_mock.assert_awaited_once()
        args, _ = persist_mock.await_args
        assert args[0] == "microsoft"
        assert args[2]["access_token"] == "fresh-access-token"

    @patch("app.services.oauth_service.OAuthService.persist_tokens", new_callable=AsyncMock)
    async def test_login_with_refresh_token_still_persists(self, persist_mock):
        """Unchanged behaviour: a login WITH a refresh_token keeps persisting."""
        service = await self._make_service()
        service._exchange_code.return_value = {
            "access_token": "fresh-access-token",
            "refresh_token": "fresh-refresh-token",
            "id_token": "ignored",
            "expires_in": 3600,
        }

        await service.handle_callback("microsoft", "code-123", "state-abc")

        persist_mock.assert_awaited_once()

    @patch("app.services.oauth_service.OAuthService.persist_tokens", new_callable=AsyncMock)
    async def test_login_without_access_token_does_not_persist(self, persist_mock):
        """No access_token at all (broken exchange) must not call persist_tokens."""
        service = await self._make_service()
        service._exchange_code.return_value = {
            "id_token": "ignored",
        }
        service._get_userinfo = AsyncMock(
            return_value={"sub": "ms-sub-1", "email": "m.mustermann@example.invalid",
                          "name": "M. Mustermann", "email_verified": True}
        )

        await service.handle_callback("microsoft", "code-123", "state-abc")

        persist_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
