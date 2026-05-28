"""Service for publishing Codex CLI auth to the shared agent volume.

Codex' ChatGPT sign-in is represented by the CLI's auth.json. We store that
JSON encrypted in oauth_integrations(provider='codex') and materialize it into
/shared/.codex/auth.json for agent containers at runtime.
"""

import json
import logging
import os

from app.core.encryption import decrypt_token
from app.db.session import async_session_factory
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from sqlalchemy import select

logger = logging.getLogger(__name__)

SHARED_CODEX_AUTH_PATH = "/shared/.codex/auth.json"
DEFAULT_AGENT_UID = 1000
DEFAULT_AGENT_GID = 1000


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
