"""Service for refreshing Claude Code OAuth tokens automatically.

Uses Anthropic's OAuth endpoint to exchange a refresh token for a new
access token before the current one expires.  Refresh tokens are single-use:
each refresh returns a *new* refresh token that replaces the old one.

After each successful refresh the new access token is written to a shared
JSON file (/shared/.auth/token.json) so that agent containers can pick it
up without needing a restart.
"""

import json
import logging
import os
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Anthropic OAuth endpoint (used by Claude Code CLI)
ANTHROPIC_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


class ClaudeTokenService:
    """Handles automatic refresh of Claude Code OAuth access tokens."""

    def __init__(self) -> None:
        self.last_refresh: datetime | None = None
        self.consecutive_failures: int = 0

    async def refresh_access_token(self) -> bool:
        """Exchange the refresh token for a new access + refresh token pair.

        Returns True on success, False on failure.
        Updates ``settings`` in-memory AND persists to the database.
        """
        refresh_token = settings.claude_code_oauth_refresh_token
        if not refresh_token:
            return False

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.post(
                    ANTHROPIC_TOKEN_URL,
                    json={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": CLAUDE_CODE_CLIENT_ID,
                    },
                )

            if resp.status_code != 200:
                logger.error(
                    f"Token refresh failed (HTTP {resp.status_code}): {resp.text}"
                )
                self.consecutive_failures += 1
                return False

            data = resp.json()
            new_access = data.get("access_token", "")
            new_refresh = data.get("refresh_token", "")

            if not new_access:
                logger.error("Token refresh response missing access_token")
                self.consecutive_failures += 1
                return False

            # Update in-memory config
            old_token_suffix = settings.claude_code_oauth_token[-8:] if settings.claude_code_oauth_token else "n/a"
            settings.claude_code_oauth_token = new_access
            if new_refresh:
                settings.claude_code_oauth_refresh_token = new_refresh

            # Persist to database
            await self._persist_tokens(new_access, new_refresh)

            # Write to shared volume so agent containers pick it up
            self._write_shared_token(new_access)

            self.last_refresh = datetime.now(timezone.utc)
            self.consecutive_failures = 0

            new_token_suffix = new_access[-8:]
            logger.info(
                f"Claude OAuth token refreshed successfully "
                f"(…{old_token_suffix} → …{new_token_suffix})"
            )
            return True

        except httpx.TimeoutException:
            logger.error("Token refresh timed out")
            self.consecutive_failures += 1
            return False
        except Exception as e:
            logger.error(f"Token refresh error: {e}")
            self.consecutive_failures += 1
            return False

    SHARED_TOKEN_PATH = "/shared/.auth/token.json"

    def _write_shared_token(self, access_token: str) -> None:
        """Write the access token to the shared volume for agent containers."""
        try:
            os.makedirs(os.path.dirname(self.SHARED_TOKEN_PATH), exist_ok=True)
            payload = {
                "access_token": access_token,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp_path = self.SHARED_TOKEN_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, self.SHARED_TOKEN_PATH)
            logger.info("Wrote refreshed token to shared volume")
        except Exception as e:
            logger.error(f"Failed to write shared token file: {e}")

    def write_initial_token(self) -> None:
        """Write the current token to shared volume (called at startup)."""
        token = settings.claude_code_oauth_token
        if token:
            self._write_shared_token(token)

    async def _persist_tokens(self, access_token: str, refresh_token: str) -> None:
        """Save the new tokens to the database (encrypted)."""
        from app.db.session import async_session_factory
        from app.services.settings_service import SettingsService

        try:
            async with async_session_factory() as db:
                svc = SettingsService(db)
                await svc.set("claude_code_oauth_token", access_token)
                if refresh_token:
                    await svc.set("claude_code_oauth_refresh_token", refresh_token)
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to persist refreshed tokens to DB: {e}")
