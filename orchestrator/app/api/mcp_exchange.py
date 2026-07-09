"""On-prem Exchange (EWS) MCP Server — per-AGENT transport.

Endpoint: POST /mcp/exchange-onprem/{agent_id}
Auth:      Authorization: Bearer <agent_hmac_token>

Tool definitions, EWS calls and JSON-RPC dispatch live in
``app.core.exchange_mcp``. This module adds agent-token auth and resolves the
**per-user** EWS connection context: the admin-configured connection settings
plus the agent OWNER's mailbox email (impersonation target).
"""

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_token
from app.core.exchange_mcp import handle_mcp_request, mcp_error
from app.db.session import async_session_factory
from app.dependencies import make_agent_token
from app.models.agent import Agent
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.models.user import User
from app.services.settings_service import SettingsService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp-exchange"])


async def _resolve_context(agent_id: str, db: AsyncSession) -> dict | None:
    """Per-user EWS connection context for the agent's owner, or None when
    Exchange isn't configured / the agent has no owner mailbox / a mode's
    prerequisites are missing."""
    svc = SettingsService(db)
    server = await svc.get("exchange_server_url")
    smtp_host = await svc.get("smtp_relay_host")
    # Two independent transports: EWS (read/calendar) and an SMTP relay (send).
    # Enterprise networks often block EWS to the mailbox server but permit SMTP to a
    # relay — so we accept either, and send goes via SMTP whenever a relay is set.
    if not server and not smtp_host:
        return None

    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent or not agent.user_id:
        return None
    user = (await db.execute(select(User).where(User.id == agent.user_id))).scalar_one_or_none()
    if not user or not user.email:
        return None

    async def _smtp_cfg() -> dict | None:
        if not smtp_host:
            return None
        raw_domains = (await svc.get("smtp_allowed_recipient_domains")) or ""
        return {
            "host": smtp_host,
            "port": int((await svc.get("smtp_relay_port")) or 25),
            "starttls": (await svc.get("smtp_relay_starttls")) != "false",
            "verify_tls": (await svc.get("smtp_relay_verify_tls")) != "false",
            "user": (await svc.get("smtp_relay_user")) or None,
            "password": (await svc.get("smtp_relay_password")) or None,
            # empty list → sender's own domain only; ["*"] → any domain
            "allowed_domains": [d.strip().lower() for d in raw_domains.split(",") if d.strip()],
        }

    ctx: dict = {"user_email": user.email, "agent_id": agent_id}
    smtp = await _smtp_cfg()
    if smtp:
        ctx["smtp"] = smtp

    if not server:
        return ctx  # SMTP-only deployment (send works, EWS read tools absent)

    mode = (await svc.get("exchange_auth_mode")) or "service_account"
    ctx.update(mode=mode, server=server)

    if mode == "modern_auth":
        client_id = await svc.get("oauth_microsoft_client_id")
        client_secret = await svc.get("oauth_microsoft_client_secret")
        tenant_id = (await svc.get("exchange_tenant_id")) or (await svc.get("oauth_microsoft_tenant_id"))
        if not (client_id and client_secret and tenant_id):
            return ctx if smtp else None  # EWS creds missing → SMTP-only if available
        ctx.update(client_id=client_id, client_secret=client_secret, tenant_id=tenant_id)
    elif mode == "basic":
        row = (await db.execute(
            select(OAuthIntegration).where(
                OAuthIntegration.provider == OAuthProvider.EXCHANGE_ONPREM,
                OAuthIntegration.user_id == agent.user_id,
            )
        )).scalar_one_or_none()
        if not row:
            return ctx if smtp else None
        try:
            password = decrypt_token(row.access_token_encrypted)
        except Exception:
            return ctx if smtp else None
        ctx.update(basic_user=row.account_label or user.email, basic_password=password)
    else:  # service_account (default)
        sa_user = await svc.get("exchange_service_account_user")
        sa_password = await svc.get("exchange_service_account_password")
        if not sa_user or not sa_password:
            return ctx if smtp else None
        ctx.update(sa_user=sa_user, sa_password=sa_password)

    return ctx


@router.post("/exchange-onprem/{agent_id}")
async def mcp_exchange_endpoint(agent_id: str, request: Request):
    """MCP Streamable HTTP endpoint for on-prem Exchange tools (agent transport)."""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not token or not hmac.compare_digest(token, make_agent_token(agent_id)):
        return JSONResponse(mcp_error(None, -32600, "Unauthorized"), status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(mcp_error(None, -32700, "Parse error"), status_code=400)

    async def resolve_context() -> dict | None:
        async with async_session_factory() as db:
            return await _resolve_context(agent_id, db)

    # Read-only by default; write mode unlocks send/create/update/delete tools.
    async with async_session_factory() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    access = (agent.config or {}).get("exchange_access", "read") if agent else "read"
    write_enabled = access in ("write", "read_write", "rw")

    resp, status = await handle_mcp_request(body, resolve_context, write_enabled=write_enabled)
    return JSONResponse(resp, status_code=status)
