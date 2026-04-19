"""Bridge download redirect endpoints."""

import os

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/download", tags=["downloads"])

GITHUB_REPO = os.getenv("GITHUB_REPO", "greeves89/AI-Employee")
BRIDGE_TAG = os.getenv("BRIDGE_RELEASE_TAG", "bridge-latest")


@router.get("/bridge/mac")
async def download_bridge_mac():
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{BRIDGE_TAG}/AI-Employee-Bridge.dmg"
    return RedirectResponse(url=url, status_code=302)


@router.get("/bridge/windows")
async def download_bridge_windows():
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{BRIDGE_TAG}/AI-Employee-Bridge-Windows.zip"
    return RedirectResponse(url=url, status_code=302)
