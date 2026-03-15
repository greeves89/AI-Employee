"""Service for managing Claude Code OAuth tokens.

Token sources (priority order):
1. /host-auth/token.json (Keychain sync from macOS host via launchd job)
2. /shared/.auth/token.json (shared volume fallback)
3. settings.claude_code_oauth_token (env var / DB fallback)

The launchd job on the host (com.ai-employee.sync-token) handles ALL token
refreshes and writes the result back to the macOS Keychain so VS Code stays
logged in. The server NEVER refreshes tokens directly — doing so would
invalidate VS Code's session.
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
    """Manages Claude OAuth tokens — reads from Keychain sync, never self-refreshes."""

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

    async def refresh_access_token(self) -> bool:
        """Read fresh token from Keychain sync file. Never self-refreshes."""
        token_data = self._read_token_data(HOST_TOKEN_PATH)
        source = "keychain"

        if not token_data:
            token_data = self._read_token_data(SHARED_TOKEN_PATH)
            source = "shared-volume"

        if not token_data:
            logger.warning(
                "No token file found — ensure launchd sync job is running on host"
            )
            return False

        token = token_data["access_token"]
        token_suffix = token[-8:]

        if token_suffix != self._last_token_suffix:
            old_suffix = (
                settings.claude_code_oauth_token[-8:]
                if settings.claude_code_oauth_token
                else "n/a"
            )
            settings.claude_code_oauth_token = token
            self._last_token_suffix = token_suffix
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
