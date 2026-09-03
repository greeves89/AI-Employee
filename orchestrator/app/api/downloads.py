"""Bridge download redirect endpoints."""

import os

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from app.config import get_agent_version

router = APIRouter(prefix="/download", tags=["downloads"])

# `or` statt eines getenv-Standards: docker-compose reicht die Variable als
# `${GITHUB_REPO:-}` weiter, setzt sie also auf LEER, wenn der Host sie nicht
# kennt. `os.getenv(name, default)` greift aber nur, wenn die Variable GAR NICHT
# existiert — eine leere Variable schlaegt den Standard. Ergebnis war ein Link
# auf `https://github.com//releases/...` (doppelter Schraegstrich, kein Repo),
# und jeder Download-Knopf endete auf einer 404-Seite.
GITHUB_REPO = os.getenv("GITHUB_REPO") or "greeves89/AI-Employee"
BRIDGE_TAG = os.getenv("BRIDGE_RELEASE_TAG") or "bridge-latest"


@router.get("/bridge/mac")
async def download_bridge_mac():
    bridge_asset = f"AI-Employee-Bridge-v{get_agent_version()}.dmg"
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{BRIDGE_TAG}/{bridge_asset}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/bridge/windows")
async def download_bridge_windows():
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{BRIDGE_TAG}/AI-Employee-Bridge-Windows.zip"
    return RedirectResponse(url=url, status_code=302)
