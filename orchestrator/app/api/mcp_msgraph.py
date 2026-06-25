"""MS Graph MCP Server — per-AGENT transport.

Endpoint: POST /mcp/msgraph/{agent_id}
Auth:      X-Agent-ID + Authorization: Bearer <agent_hmac_token>

Tool definitions, Graph calls, and JSON-RPC dispatch live in
``app.core.msgraph_mcp`` (shared with the external per-user transport in
``mcp_msgraph_external.py``). This module only adds agent-token auth and resolves
the Microsoft access token from the agent's OWNER's connected account.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.dependencies import verify_agent_token
from app.models.agent import Agent
from app.services.oauth_service import OAuthService
from app.core.msgraph_mcp import handle_mcp_request, mcp_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp-msgraph"])


async def _get_access_token(agent_id: str, db: AsyncSession) -> str | None:
    """Valid Microsoft access token for the agent's owner user (auto-refreshed).

    Returns None when the agent has no owner, the owner has not connected M365,
    or the token is expired without a refresh token — all of which the MCP
    dispatch surfaces to the model as "Microsoft account not connected".
    """
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent or not agent.user_id:
        return None
    try:
        return await OAuthService(db).get_valid_token("microsoft", agent.user_id)
    except ValueError:
        return None


@router.post("/msgraph/{agent_id}")
async def mcp_msgraph_endpoint(agent_id: str, request: Request):
    """MCP Streamable HTTP endpoint for MS Graph tools (agent transport)."""
    try:
        auth_info = await verify_agent_token(request)
        if auth_info["agent_id"] != agent_id:
            return JSONResponse(mcp_error(None, -32600, "Agent ID mismatch"), status_code=403)
    except Exception:
        return JSONResponse(mcp_error(None, -32600, "Unauthorized"), status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(mcp_error(None, -32700, "Parse error"), status_code=400)

    async def resolve_token() -> str | None:
        async with async_session_factory() as db:
            return await _get_access_token(agent_id, db)

    resp, status = await handle_mcp_request(body, resolve_token)
    return JSONResponse(resp, status_code=status)
