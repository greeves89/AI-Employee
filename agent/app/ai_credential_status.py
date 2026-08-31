"""Report the real runtime status of the owner's AI subscription credential."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)

AUTH_ERROR_MARKERS = (
    "does not have access",
    "invalid_grant",
    "unauthorized",
    "401",
    "oauth",
    "authentication",
    "revoked",
    "token_expired",
    "refresh_token_reused",
)


def is_auth_error(error: str | None) -> bool:
    text = (error or "").lower()
    return any(marker in text for marker in AUTH_ERROR_MARKERS)


async def report_ai_credential_status(status: str) -> None:
    """Best-effort status feedback to the orchestrator.

    The orchestrator resolves the owner and harness from the authenticated agent,
    so the agent cannot choose a user or mark a shared team credential.
    """
    if status not in {"ok", "auth_failed"}:
        return
    agent_id = settings.agent_id
    token = settings.agent_token
    if not agent_id or not token:
        return

    url = f"{settings.orchestrator_url}/api/v1/agents/{agent_id}/ai-credential-status"
    body = json.dumps({"status": status}).encode("utf-8")

    def _post() -> None:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Agent-ID": agent_id,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            response.read()

    try:
        await asyncio.to_thread(_post)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("[Zugang] Statusmeldung %s fehlgeschlagen: %s", status, exc)


async def report_result_status(result: dict) -> None:
    """Report only outcomes that prove something about the credential."""
    if result.get("status") == "completed":
        await report_ai_credential_status("ok")
        return
    if result.get("status") == "error" and is_auth_error(str(result.get("error", ""))):
        await report_ai_credential_status("auth_failed")
