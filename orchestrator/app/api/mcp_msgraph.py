"""MS Graph MCP Server — per-AGENT transport.

Endpoint: POST /mcp/msgraph/{agent_id}
Auth:      X-Agent-ID + Authorization: Bearer <agent_hmac_token>

Tool definitions, Graph calls, and JSON-RPC dispatch live in
``app.core.msgraph_mcp`` (shared with the external per-user transport in
``mcp_msgraph_external.py``). This module only adds agent-token auth and resolves
the Microsoft access token from the agent's OWNER's connected account.
"""

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.dependencies import make_agent_token
from app.models.agent import Agent
from app.services.oauth_service import OAuthService
from app.core import ms_access
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
        # OAuthService needs (db, redis); get_valid_token doesn't use redis → None ok.
        return await OAuthService(db, None).get_valid_token("microsoft", agent.user_id)
    except Exception as e:
        # Not connected / no refresh token (ValueError) OR a transient Microsoft
        # outage during refresh — both surface to the model as "not connected".
        logger.warning("MS Graph token unavailable for agent %s: %s", agent_id, e)
        return None


@router.post("/msgraph/{agent_id}")
async def mcp_msgraph_endpoint(agent_id: str, request: Request):
    """MCP Streamable HTTP endpoint for MS Graph tools (agent transport)."""
    # Auth: the agent_id is already in the URL path; the agent's MCP client sends
    # only `Authorization: Bearer <token>` (no X-Agent-ID header), so verify the
    # bearer directly against the HMAC token derived from the path agent_id.
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, make_agent_token(agent_id)):
        return JSONResponse(mcp_error(None, -32600, "Unauthorized"), status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(mcp_error(None, -32700, "Parse error"), status_code=400)

    # Der Token beweist nur, WELCHER Agent fragt — nicht, dass er an das Postfach darf.
    from app.core.faehigkeiten import anbindung_zugewiesen
    async with async_session_factory() as db:
        if not await anbindung_zugewiesen(db, agent_id, "microsoft"):
            return JSONResponse(mcp_error(body.get("id"), -32600,
                                          "Die Microsoft-Anbindung ist diesem Agenten nicht zugewiesen."), status_code=403)

    async def resolve_token() -> str | None:
        async with async_session_factory() as db:
            return await _get_access_token(agent_id, db)

    # Determine the agent's Microsoft access mode (read-only by default). Write mode
    # unlocks the send/create tools; outbound mail is sent for real. The platform-wide
    # read-only switch overrides the agent's setting — see app.core.ms_access.
    async with async_session_factory() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    may_write = ms_access.write_enabled(agent.config if agent else None, "msgraph_access")
    im_sekretariat = False
    if agent is not None:
        from app.core.sekretariat import ist_sekretariats_agent

        async with async_session_factory() as db:
            im_sekretariat = await ist_sekretariats_agent(db, agent_id)
    if im_sekretariat and may_write:
        # Fail-closed: Im Sekretariat geht keine Mail ohne Freigabe raus — auch wenn die
        # Einstellung des Agenten später auf „Lesen + Schreiben“ gestellt wurde.
        may_write = False
    entwurf = None
    # Die Mitgliedschaft im Sekretariat stuft nur HERAB (Schreiben → Entwurf), sie gibt nie
    # mehr Rechte als die eigene Einstellung des Agenten: „Lesen“ bleibt „Lesen“.
    if not may_write and (ms_access.entwurf_enabled(agent.config if agent else None)
                          or (im_sekretariat and not ms_access.read_only_enabled()
                              and (agent.config or {}).get("msgraph_access") in ms_access._WRITE_VALUES)):
        from app.core.mail_freigabe import anfragen

        async def entwurf(message_id: str, betreff: str) -> str:
            return await anfragen(agent_id, message_id, betreff)

    verschieben = (im_sekretariat and body.get("method") == "tools/call"
                   and (body.get("params") or {}).get("name") == "ms_move_email")
    betreff = await _betreff_vor_dem_verschieben(body, resolve_token) if verschieben else None
    resp, status = await handle_mcp_request(body, resolve_token, write_enabled=may_write, entwurf_freigabe=entwurf)
    if verschieben and not ((resp.get("result") or {}).get("isError")):
        await _verschieben_protokollieren(agent, body, betreff)
    return JSONResponse(resp, status_code=status)


async def _betreff_vor_dem_verschieben(body: dict, resolve_token) -> str | None:
    """Betreff der Mail, die ein Sekretariats-Agent gleich verschiebt — fürs Protokoll.
    Danach trägt die Mail eine neue Kennung; deshalb vorher."""
    from app.core.msgraph_mcp import _gid, _graph

    mail_id = ((body.get("params") or {}).get("arguments") or {}).get("email_id")
    try:
        token = await resolve_token()
        if not token or not mail_id:
            return None
        return (await _graph("GET", f"/me/messages/{_gid(mail_id)}", token, params={"$select": "subject"})).get("subject")
    except Exception:  # noqa: BLE001 — das Protokoll darf das Verschieben nie verhindern
        return None


async def _verschieben_protokollieren(agent, body: dict, betreff: str | None) -> None:
    """Protokoll „Was das Sekretariat heute getan hat“ (core/sekretariat.protokoll)."""
    from app.core.sekretariat import protokollieren

    ziel = str(((body.get("params") or {}).get("arguments") or {}).get("folder") or "").strip()
    namen = {"archive": "Archiv", "deleteditems": "Gelöschte Elemente", "junkemail": "Junk-E-Mail"}
    ziel = namen.get(ziel.lower(), ziel if len(ziel) <= 40 else "einen eigenen Ordner")
    try:
        async with async_session_factory() as db:
            await protokollieren(db, str(agent.user_id or ""), "verschoben",
                                 f"Mail „{(betreff or '(ohne Betreff)')[:200]}“ nach „{ziel}“ verschoben",
                                 agent_id=agent.id)
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Sekretariat: Verschieben nicht protokolliert: %s", e)
