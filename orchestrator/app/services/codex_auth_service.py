"""Service for publishing Codex CLI auth to the shared agent volume.

Codex' ChatGPT sign-in is represented by the CLI's auth.json. We store that
JSON encrypted in oauth_integrations(provider='codex') and materialize it into
/shared/.codex/auth.json for agent containers at runtime.

The ChatGPT refresh token is SINGLE-USE (rotates on every refresh). All Codex
agents share this one auth.json, so if several refresh it concurrently (e.g. a
simultaneous "Update All"), the first rotates it and the rest die with
``refresh_token_reused``. To prevent that we refresh the token CENTRALLY and
proactively here (single-threaded, well before expiry), so the shared file is
always valid and individual agents never need to refresh it themselves.
"""

import base64
import hashlib
import json
import logging
import os
import subprocess
import time

from app.core.encryption import decrypt_token, encrypt_token
from app.db.session import async_session_factory
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from sqlalchemy import select

logger = logging.getLogger(__name__)

SHARED_CODEX_AUTH_PATH = "/shared/.codex/auth.json"
SHARED_CODEX_HOME = "/shared/.codex"
DEFAULT_AGENT_UID = 1000
DEFAULT_AGENT_GID = 1000

# Refresh the shared token when its access token has less than this long left.
# The access token lives ~10 days, so this fires roughly once every ~8 days,
# single-threaded, long before any agent would hit an expired token.
REFRESH_LEEWAY_SECONDS = 48 * 3600


def _agent_file_owner() -> tuple[int, int]:
    """Return the UID/GID used by the non-root agent user in agent containers."""
    try:
        uid = int(os.environ.get("AGENT_CONTAINER_UID", str(DEFAULT_AGENT_UID)))
        gid = int(os.environ.get("AGENT_CONTAINER_GID", str(DEFAULT_AGENT_GID)))
        return uid, gid
    except ValueError:
        return DEFAULT_AGENT_UID, DEFAULT_AGENT_GID


def _make_agent_readable(path: str) -> None:
    """Let the non-root agent user read Codex auth without making it world-readable."""
    uid, gid = _agent_file_owner()
    os.chown(path, uid, gid)
    os.chmod(path, 0o600)


def _access_token_exp(parsed: dict) -> int | None:
    """Extract the access-token expiry (unix seconds) from the auth.json JWT."""
    tok = (parsed.get("tokens") or {}).get("access_token", "")
    if isinstance(tok, str) and tok.count(".") == 2:
        try:
            payload = tok.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        except Exception:  # noqa: BLE001 — malformed token → treat as unknown
            return None
    return None


def _file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class CodexAuthService:
    async def sync_auth_json(self) -> bool:
        """Write the encrypted Codex auth.json from DB to the shared volume."""
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(OAuthIntegration).where(
                        OAuthIntegration.provider == OAuthProvider.CODEX
                    )
                )
                integration = result.scalar_one_or_none()
                if not integration:
                    return False

                auth_json = decrypt_token(integration.access_token_encrypted)
                parsed = json.loads(auth_json)
                os.makedirs(os.path.dirname(SHARED_CODEX_AUTH_PATH), exist_ok=True)
                tmp_path = SHARED_CODEX_AUTH_PATH + ".tmp"
                with open(tmp_path, "w") as f:
                    json.dump(parsed, f)
                _make_agent_readable(tmp_path)
                os.replace(tmp_path, SHARED_CODEX_AUTH_PATH)
                logger.info("Synced Codex auth.json to shared agent volume")
                return True
        except Exception as e:
            logger.warning("Failed to sync Codex auth.json: %s", e)
            return False

    async def _store_shared_to_db(self) -> None:
        """Persist the (possibly refreshed) shared auth.json back into the DB, so the
        rotated refresh token is never lost and later syncs use the current one."""
        with open(SHARED_CODEX_AUTH_PATH) as f:
            parsed = json.load(f)
        async with async_session_factory() as db:
            integration = (
                await db.execute(
                    select(OAuthIntegration).where(
                        OAuthIntegration.provider == OAuthProvider.CODEX
                    )
                )
            ).scalar_one_or_none()
            if not integration:
                return
            integration.access_token_encrypted = encrypt_token(json.dumps(parsed))
            await db.commit()

    async def ensure_fresh(self) -> bool:
        """Keep the shared Codex token valid, CENTRALLY, so agents never refresh it.

        - Materializes the token from the DB if the shared file is missing.
        - If the access token still has plenty of life left, does nothing.
        - Otherwise forces a single, central refresh via a trivial ``codex exec``
          (which rotates the token and rewrites the shared auth.json), then persists
          the rotated token back to the DB. Runs single-threaded from the scheduler,
          so there is never a concurrent refresh to collide with.
        """
        try:
            if not os.path.exists(SHARED_CODEX_AUTH_PATH):
                return await self.sync_auth_json()

            with open(SHARED_CODEX_AUTH_PATH) as f:
                parsed = json.load(f)
            exp = _access_token_exp(parsed)
            if exp and (exp - time.time()) > REFRESH_LEEWAY_SECONDS:
                return True  # still fresh — nothing to do

            logger.info("Codex access token near expiry → central refresh")
            before = _file_hash(SHARED_CODEX_AUTH_PATH)
            env = {**os.environ, "CODEX_HOME": SHARED_CODEX_HOME}
            try:
                subprocess.run(
                    ["codex", "exec", "--skip-git-repo-check", "Reply with OK"],
                    env=env, cwd="/tmp", capture_output=True, timeout=120,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Central Codex refresh exec failed: %s", e)
                return False

            _make_agent_readable(SHARED_CODEX_AUTH_PATH)
            if _file_hash(SHARED_CODEX_AUTH_PATH) != before:
                await self._store_shared_to_db()
                logger.info("Codex token centrally refreshed + re-stored to DB")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("Codex ensure_fresh failed: %s", e)
            return False
