"""Service for managing Claude Code OAuth tokens.

Token sources (priority order):
1. /host-auth/token.json (Keychain sync from macOS host - preferred)
2. /shared/.auth/token.json (shared volume between orchestrator + agents)
3. settings.claude_code_oauth_token (env var / DB fallback)

Self-refresh: When the access token is expired but a refresh_token is
available, the service refreshes it via Anthropic's OAuth endpoint.
The refreshed token is written back to both shared paths so agents pick
it up immediately.
"""

import json
import logging
import os
import time

import httpx

from datetime import datetime, timezone

from app.config import settings

logger = logging.getLogger(__name__)

HOST_TOKEN_PATH = "/host-auth/token.json"
SHARED_TOKEN_PATH = "/shared/.auth/token.json"

# Claude Code OAuth endpoints (extracted from Claude Code CLI source)
ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


class ClaudeTokenService:
    """Manages Claude OAuth tokens with Keychain sync + self-refresh."""

    def __init__(self) -> None:
        self.last_refresh: datetime | None = None
        self.consecutive_failures: int = 0
        self._last_token_suffix: str = ""
        self._refresh_token: str = ""

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

    def _is_expired(self, expires_at: int | float) -> bool:
        """Check if token needs refresh (1 hour before actual expiry)."""
        if not expires_at:
            return False  # No expiry info = assume valid
        now_ms = time.time() * 1000
        buffer_ms = 60 * 60 * 1000  # Refresh 1 hour before expiry
        return now_ms > (expires_at - buffer_ms)

    async def _do_refresh(self, refresh_token: str) -> dict | None:
        """Refresh the access token via Anthropic OAuth endpoint."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    ANTHROPIC_TOKEN_URL,
                    json={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": ANTHROPIC_CLIENT_ID,
                    },
                )
                if resp.status_code != 200:
                    logger.error(
                        f"Token refresh failed ({resp.status_code}): {resp.text[:200]}"
                    )
                    return None

                data = resp.json()
                access_token = data.get("access_token", "")
                new_refresh = data.get("refresh_token", refresh_token)
                expires_in = data.get("expires_in", 3600)

                if not access_token:
                    logger.error("Token refresh returned empty access_token")
                    return None

                expires_at_ms = int((time.time() + expires_in) * 1000)
                logger.info(
                    f"Token refreshed successfully "
                    f"(…{access_token[-8:]}, expires in {expires_in}s)"
                )
                return {
                    "access_token": access_token,
                    "refresh_token": new_refresh,
                    "expires_at": expires_at_ms,
                }
        except Exception as e:
            logger.error(f"Token refresh request failed: {e}")
            return None

    async def refresh_access_token(self) -> bool:
        """Read fresh token from Keychain file, self-refresh if expired."""
        # Priority 1: Host-mounted Keychain sync file
        token_data = self._read_token_data(HOST_TOKEN_PATH)
        source = "keychain"

        # Priority 2: Shared volume
        if not token_data:
            token_data = self._read_token_data(SHARED_TOKEN_PATH)
            source = "shared-volume"

        if not token_data:
            self.consecutive_failures += 1
            if self.consecutive_failures <= 1:
                logger.warning(
                    "No token file found — run scripts/sync-token.sh on the host"
                )
            return False

        token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token", "") or self._refresh_token
        expires_at = token_data.get("expires_at", 0)

        # Store refresh token for future use
        if refresh_token:
            self._refresh_token = refresh_token

        # Check if token is expired and we can self-refresh
        if self._is_expired(expires_at) and refresh_token:
            logger.info(
                f"Access token expired (…{token[-8:]}), "
                f"refreshing via Anthropic OAuth..."
            )
            refreshed = await self._do_refresh(refresh_token)
            if refreshed:
                token = refreshed["access_token"]
                self._refresh_token = refreshed.get("refresh_token", refresh_token)
                expires_at = refreshed["expires_at"]
                source = "self-refreshed"

                # Write refreshed token back to files
                self._write_token_files(
                    token, self._refresh_token, expires_at
                )
            else:
                logger.warning("Self-refresh failed, using expired token as fallback")

        token_suffix = token[-8:]

        # Only update settings if token actually changed
        if token_suffix != self._last_token_suffix:
            old_suffix = (
                settings.claude_code_oauth_token[-8:]
                if settings.claude_code_oauth_token
                else "n/a"
            )
            settings.claude_code_oauth_token = token
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

    def _write_token_files(
        self, access_token: str, refresh_token: str, expires_at: int
    ) -> None:
        """Write token data to shared volume (and host-auth if writable)."""
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "self-refreshed",
        }

        for path in [SHARED_TOKEN_PATH, HOST_TOKEN_PATH]:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                tmp_path = path + ".tmp"
                with open(tmp_path, "w") as f:
                    json.dump(payload, f)
                os.replace(tmp_path, path)
            except Exception as e:
                logger.debug(f"Could not write {path}: {e}")

    def _write_shared_token(self, access_token: str) -> None:
        """Write token to shared volume for agent containers."""
        try:
            os.makedirs(os.path.dirname(SHARED_TOKEN_PATH), exist_ok=True)
            payload = {
                "access_token": access_token,
                "refresh_token": self._refresh_token,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            tmp_path = SHARED_TOKEN_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(payload, f)
            os.replace(tmp_path, SHARED_TOKEN_PATH)
        except Exception as e:
            logger.error(f"Failed to write shared token file: {e}")

    def write_initial_token(self) -> None:
        """Load token from files at startup and write to shared volume."""
        # Try Keychain file first
        token_data = self._read_token_data(HOST_TOKEN_PATH)
        if token_data:
            token = token_data["access_token"]
            settings.claude_code_oauth_token = token
            self._last_token_suffix = token[-8:]
            self._refresh_token = token_data.get("refresh_token", "")
            logger.info(
                f"Loaded initial token from Keychain sync "
                f"(…{token[-8:]}, refresh={'yes' if self._refresh_token else 'no'})"
            )
        else:
            token = settings.claude_code_oauth_token

        if token:
            self._write_shared_token(token)
