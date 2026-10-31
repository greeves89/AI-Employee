"""E-Mail-MCP fuer Agenten (Gmail) — Endpoint ``POST /mcp/email/{agent_id}``.

Auth wie ``/mcp/msgraph/{agent_id}``: ``Authorization: Bearer <agent_hmac_token>``,
gegen den Token der Agenten-ID im Pfad geprueft. Das Google-Token des Besitzers
bleibt im Orchestrator (``core/gmail_mcp.py``).
"""

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core import faehigkeiten
from app.core.gmail_mcp import handle_mcp_request
from app.core.msgraph_mcp import mcp_error
from app.db.session import async_session_factory
from app.dependencies import make_agent_token
from app.models.agent import Agent
from app.services.oauth_service import OAuthService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp-email"])


@router.post("/email/{agent_id}")
async def mcp_email_endpoint(agent_id: str, request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, make_agent_token(agent_id)):
        return JSONResponse(mcp_error(None, -32600, "Unauthorized"), status_code=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(mcp_error(None, -32700, "Parse error"), status_code=400)

    async with async_session_factory() as db:
        besitzer = (await db.execute(select(Agent.user_id).where(Agent.id == agent_id))).scalar_one_or_none()
        senden = await faehigkeiten.zustand(db, agent_id, "email_m365")

    async def resolve_token() -> str | None:
        if not besitzer:
            return None
        async with async_session_factory() as db:
            return await OAuthService(db, None).get_valid_token("google", besitzer)

    resp, status = await handle_mcp_request(body, resolve_token, senden=senden)
    return JSONResponse(resp, status_code=status)
