"""Service for managing Claude Code OAuth tokens.

Token sources (priority order):
1. DB: oauth_integrations table (provider='anthropic') — BOT'S OWN SESSION
2. /host-auth/token.json (Keychain sync from macOS host — LEGACY FALLBACK)
3. settings.claude_code_oauth_token (env var / manual paste — LAST RESORT)

The preferred flow is: user logs in via WebUI → tokens stored encrypted in DB
→ background task auto-refreshes → agents get fresh token from shared volume.
"""

import json
import logging
import os

from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

HOST_TOKEN_PATH = "/host-auth/token.json"
SHARED_TOKEN_PATH = "/shared/.auth/token.json"


class ClaudeTokenService:
    """Manages Claude OAuth tokens — prefers DB (own session), falls back to Keychain sync."""

    def __init__(self) -> None:
        self._last_token_suffix: str = ""

    def _read_token_data(self, path: str) -> dict | None:
        """Read full token data from a token.json file."""
        try:
            if not os.path.exists(path):
                return None
            with open(path) as f:
                data = json.load(f)
            if not data.get("access_token"):
                return None
            return data
        except Exception as e:
            logger.debug(f"Failed to read {path}: {e}")
            return None

    async def _get_db_token(self) -> str | None:
        """Read token from DB (oauth_integrations, provider='anthropic').

        This is the bot's OWN OAuth session — independent of VS Code.
        Auto-refresh is handled by the existing _refresh_oauth_tokens background task.
        """
        try:
            from app.db.session import async_session_factory
            from app.models.oauth_integration import OAuthIntegration, OAuthProvider
            from app.core.encryption import decrypt_token
            from sqlalchemy import select

            async with async_session_factory() as db:
                result = await db.execute(
                    select(OAuthIntegration).where(
                        OAuthIntegration.provider == OAuthProvider.ANTHROPIC
                    )
                )
                integration = result.scalar_one_or_none()
                if not integration:
                    return None

                token = decrypt_token(integration.access_token_encrypted)
                if token:
                    return token
        except Exception as e:
            logger.debug(f"Failed to read Anthropic token from DB: {e}")
        return None

    async def refresh_access_token(self) -> bool:
        """Get the best available token and propagate to shared volume.

        Priority:
        1. DB (anthropic integration) — bot's own session
        2. /host-auth/token.json (Keychain sync)
        3. settings.claude_code_oauth_token (env/manual)
        """
        token = None
        source = "unknown"

        # Priority 1: DB (own OAuth session)
        db_token = await self._get_db_token()
        if db_token:
            token = db_token
            source = "db (own session)"

        # Priority 2: Keychain sync file
        if not token:
            token_data = self._read_token_data(HOST_TOKEN_PATH)
            if token_data:
                token = token_data["access_token"]
                source = "keychain"

        # Priority 3: Env/manual
        if not token and settings.claude_code_oauth_token:
            token = settings.claude_code_oauth_token
            source = "settings"

        if not token:
            logger.warning("No Claude token found in DB, Keychain file, or settings")
            return False

        token_suffix = token[-8:]

        if token_suffix != self._last_token_suffix:
            old_suffix = (
                settings.claude_code_oauth_token[-8:]
                if settings.claude_code_oauth_token
                else "n/a"
            )
            settings.claude_code_oauth_token = token
            self._last_token_suffix = token_suffix
            self._write_shared_token(token)
            logger.info(
                f"Claude token updated from {source} "
                f"(…{old_suffix} → …{token_suffix})"
            )

        return True

    def write_initial_token(self) -> None:
        """Load token from files at startup and write to shared volume."""
        token_data = self._read_token_data(HOST_TOKEN_PATH)
        if token_data:
            token = token_data["access_token"]
            settings.claude_code_oauth_token = token
            self._last_token_suffix = token[-8:]
            logger.info(
                f"Loaded initial token from Keychain sync (…{token[-8:]})"
            )
        else:
            token = settings.claude_code_oauth_token

        if token:
            self._write_shared_token(token)

    def _write_shared_token(self, access_token: str) -> None:
        """Write token to shared volume for agent containers."""
        try:
            os.makedirs(os.path.dirname(SHARED_TOKEN_PATH), exist_ok=True)
            payload = {
                "access_token": access_token,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp_path = SHARED_TOKEN_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, SHARED_TOKEN_PATH)
        except Exception as e:
            logger.error(f"Failed to write shared token file: {e}")
