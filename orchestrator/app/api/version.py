"""Version check endpoint — compares local version with GitHub latest."""

import logging

import httpx
from fastapi import APIRouter

from app.config import AGENT_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/version", tags=["version"])

GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/greeves89/AI-Employee/main/VERSION"
)


@router.get("/")
async def check_version():
    """Return current version and check GitHub for updates."""
    remote_version: str | None = None
    update_available = False

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(GITHUB_RAW_URL)
            if resp.status_code == 200:
                remote_version = resp.text.strip()
                update_available = remote_version != AGENT_VERSION
    except Exception as e:
        logger.debug(f"Version check failed: {e}")

    return {
        "current": AGENT_VERSION,
        "latest": remote_version,
        "update_available": update_available,
    }
