"""Version check endpoint — compares local version with GitHub latest."""

import base64
import logging
import os

import httpx
from fastapi import APIRouter

from app.config import AGENT_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/version", tags=["version"])

GITHUB_API_URL = (
    "https://api.github.com/repos/greeves89/AI-Employee/contents/VERSION"
)
GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/greeves89/AI-Employee/main/VERSION"
)


async def _get_github_token() -> str:
    """Try to get a GitHub PAT from env or from the OAuth integrations DB."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token

    try:
        from sqlalchemy import select
        from app.db.session import async_session_factory
        from app.models.oauth_integration import OAuthIntegration, OAuthProvider
        from app.core.encryption import decrypt_token

        async with async_session_factory() as db:
            result = await db.execute(
                select(OAuthIntegration).where(
                    OAuthIntegration.provider == OAuthProvider.GITHUB
                )
            )
            integration = result.scalar_one_or_none()
            if integration and integration.access_token_encrypted:
                return decrypt_token(integration.access_token_encrypted)
    except Exception:
        pass

    return ""


async def _fetch_latest_version() -> str | None:
    """Fetch latest VERSION from GitHub."""
    gh_token = await _get_github_token()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Private repo: use API with token
            if gh_token:
                resp = await client.get(
                    GITHUB_API_URL,
                    headers={
                        "Accept": "application/vnd.github.v3+json",
                        "Authorization": f"Bearer {gh_token}",
                    },
                )
                if resp.status_code == 200:
                    content = resp.json().get("content", "")
                    return base64.b64decode(content).decode().strip()

            # Public repo fallback
            resp = await client.get(GITHUB_RAW_URL)
            if resp.status_code == 200:
                return resp.text.strip()
    except Exception as e:
        logger.debug(f"Version check failed: {e}")

    return None


@router.get("/")
async def check_version():
    """Return current version and check GitHub for updates."""
    remote_version = await _fetch_latest_version()
    update_available = bool(remote_version and remote_version != AGENT_VERSION)

    return {
        "current": AGENT_VERSION,
        "latest": remote_version,
        "update_available": update_available,
    }
