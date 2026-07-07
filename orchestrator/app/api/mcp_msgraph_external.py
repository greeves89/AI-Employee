"""MS Graph MCP Server — external per-USER transport for OpenWebUI & co.

Endpoint: POST /api/v1/mcp/msgraph
Auth:     OAuth 2.1 Bearer token issued by our built-in AS (mcp_oauth). The token
          is audience-bound to this resource and carries the platform user_id; we
          resolve that user's connected Microsoft account and call Graph as them.

Acts as an OAuth 2.0 Resource Server (RFC 9728): an unauthenticated request gets a
401 with a ``WWW-Authenticate`` header pointing at the Protected Resource Metadata,
which is how MCP clients discover where to log in. Gated behind the admin toggle.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import mcp_oauth as oas
from app.core.msgraph_mcp import handle_mcp_request, mcp_error
from app.core.oauth_providers import get_provider, is_provider_available
from app.db.session import get_db
from app.dependencies import get_redis_service
from app.services.oauth_service import OAuthService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mcp", tags=["mcp-msgraph-external"])


def _enabled() -> bool:
    if not getattr(settings, "msgraph_mcp_external_enabled", False):
        return False
    try:
        return is_provider_available(get_provider("microsoft"))
    except Exception:
        return False


def _unauthorized() -> JSONResponse:
    """401 that tells the MCP client where to find the auth server (RFC 9728)."""
    return JSONResponse(
        mcp_error(None, -32001, "Unauthorized"),
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{oas.prm_url()}"'},
    )


@router.post("/msgraph")
async def mcp_msgraph_external(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    if not _enabled():
        return JSONResponse(mcp_error(None, -32601, "Not found"), status_code=404)

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return _unauthorized()
    try:
        user_id = oas.verify_access_token(auth[7:])
    except Exception:
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(mcp_error(None, -32700, "Parse error"), status_code=400)

    async def resolve_token() -> str | None:
        try:
            return await OAuthService(db, redis).get_valid_token("microsoft", user_id)
        except Exception as e:
            # Not connected / no refresh token OR a transient Microsoft outage —
            # both surface to the client as "not connected".
            logger.warning("MS Graph token unavailable for user %s: %s", user_id, e)
            return None

    # External (OpenWebUI) access is ALWAYS read-only — no write/send tools.
    resp, status = await handle_mcp_request(body, resolve_token, write_enabled=False)
    return JSONResponse(resp, status_code=status)
