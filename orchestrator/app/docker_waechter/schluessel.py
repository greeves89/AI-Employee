"""Dienst-Schlüssel zwischen Orchestrator und Docker-Wächter.

``DOCKER_WAECHTER_TOKEN`` (legen setup.sh/update.sh an). Fehlt er — etwa in der
Community-Variante, die ohne Skripte installiert wird —, leiten beide Seiten ihn
gleich aus ``API_SECRET_KEY`` ab (eigener Zweck, HMAC), statt dass Agenten-Apps
ohne Erklärung scheitern.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_ZWECK = b"ai-employee/docker-waechter/v1"


def waechter_schluessel() -> str:
    gesetzt = os.environ.get("DOCKER_WAECHTER_TOKEN", "")
    if len(gesetzt) >= 32:
        return gesetzt
    from app.config import settings

    basis = str(getattr(settings, "api_secret_key", "") or "")
    if len(basis) < 32 or basis == "change-me-in-production":
        return ""
    return hmac.new(basis.encode(), _ZWECK, hashlib.sha256).hexdigest()


def ueber_waechter() -> bool:
    """Spricht dieser Orchestrator Docker über den Wächter?"""
    return "docker-waechter" in os.environ.get("DOCKER_HOST", "")
