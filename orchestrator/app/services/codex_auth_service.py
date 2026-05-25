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
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, SHARED_CODEX_AUTH_PATH)
                logger.info("Synced Codex auth.json to shared agent volume")
                return True
        except Exception as e:
            logger.warning("Failed to sync Codex auth.json: %s", e)
            return False
