"""Service for reading Claude Code OAuth tokens from the host Keychain sync.

Instead of refreshing tokens ourselves (which causes race conditions with
the user's local Claude Code CLI), we read the token from a host-mounted
file that is kept up-to-date by a launchd job running sync-token.sh.

The sync script reads from macOS Keychain → writes to host-auth/token.json
→ mounted read-only at /host-auth/token.json in the container.

Fallback chain:
1. /host-auth/token.json (Keychain sync - preferred, always fresh)
2. /shared/.auth/token.json (legacy shared volume)
3. settings.claude_code_oauth_token (env var / DB)
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
    """Reads Claude OAuth tokens from the Keychain-synced file."""

    def __init__(self) -> None:
        self.last_refresh: datetime | None = None
        self.consecutive_failures: int = 0
        self._last_token_suffix: str = ""

    def _read_token_file(self, path: str) -> str | None:
        """Read access_token from a token.json file."""
        try:
            if not os.path.exists(path):
                return None
            with open(path) as f:
                data = json.load(f)
            token = data.get("access_token", "")
            if not token:
                return None
            return token
        except Exception as e:
            logger.debug(f"Failed to read {path}: {e}")
            return None

    async def refresh_access_token(self) -> bool:
        """Read fresh token from Keychain-synced file.

        This replaces the old Anthropic OAuth refresh — we now just read
        the token that the host sync-token.sh script writes from Keychain.
        """
        # Priority 1: Host-mounted Keychain sync file
        token = self._read_token_file(HOST_TOKEN_PATH)
        source = "keychain"

        # Priority 2: Shared volume (legacy)
        if not token:
            token = self._read_token_file(SHARED_TOKEN_PATH)
            source = "shared-volume"

        if not token:
            self.consecutive_failures += 1
            if self.consecutive_failures <= 1:
                logger.warning(
                    "No token file found at /host-auth/token.json — "
                    "run scripts/sync-token.sh on the host"
                )
            return False

        token_suffix = token[-8:]

        # Only update if token actually changed
        if token_suffix != self._last_token_suffix:
            old_suffix = settings.claude_code_oauth_token[-8:] if settings.claude_code_oauth_token else "n/a"
            settings.claude_code_oauth_token = token
            self._write_shared_token(token)
            self._last_token_suffix = token_suffix
            self.last_refresh = datetime.now(timezone.utc)
            self.consecutive_failures = 0
            logger.info(
                f"Claude token updated from {source} "
                f"(…{old_suffix} → …{token_suffix})"
            )
        else:
            self.consecutive_failures = 0

        return True

    def _write_shared_token(self, access_token: str) -> None:
        """Write the access token to the shared volume for agent containers."""
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

    def write_initial_token(self) -> None:
        """Write the current token to shared volume (called at startup)."""
        # Try Keychain file first
        token = self._read_token_file(HOST_TOKEN_PATH)
        if token:
            settings.claude_code_oauth_token = token
            self._last_token_suffix = token[-8:]
            logger.info(f"Loaded initial token from Keychain sync (…{token[-8:]})")

        token = token or settings.claude_code_oauth_token
        if token:
            self._write_shared_token(token)
