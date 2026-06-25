"""OAuth 2.1 Authorization Server endpoints for the external MS Graph MCP server.

Two routers:
  * ``oauth_router``    — mounted under /api/v1 → /api/v1/oauth/{register,authorize,token}
  * ``wellknown_router``— mounted at ROOT     → /.well-known/oauth-{authorization-server,protected-resource}

Everything is gated behind the admin toggle ``msgraph_mcp_external_enabled`` AND a
configured Microsoft app registration — when off, all routes return 404.
"""

import hashlib
import hmac
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import decode_token
from app.core.oauth_providers import get_provider, is_provider_available
from app.core import mcp_oauth as oas
from app.db.session import get_db
from app.dependencies import get_redis_service
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

oauth_router = APIRouter(prefix="/oauth", tags=["oauth-as"])
wellknown_router = APIRouter(tags=["oauth-as"])


# ---------------------------------------------------------------------------
# Gating + helpers
# ---------------------------------------------------------------------------

def _external_enabled() -> bool:
    if not getattr(settings, "msgraph_mcp_external_enabled", False):
        return False
    try:
        return is_provider_available(get_provider("microsoft"))
    except Exception:
        return False


def _require_enabled() -> None:
    if not _external_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _session_user_id(request: Request) -> str | None:
    """Resolve the current platform user from the existing session (httpOnly JWT
    cookie or Bearer). Returns None if not logged in — we never authenticate here."""
    tok = request.cookies.get("access_token")
    if not tok:
        ah = request.headers.get("Authorization", "")
        if ah.startswith("Bearer "):
            tok = ah[7:]
    if not tok:
        return None
    try:
        data = decode_token(tok)
        if data.get("type") != "access":
            return None
        return data.get("sub")
    except Exception:
        return None


def _csrf(user_id: str, client_id: str) -> str:
    msg = f"{user_id}:{client_id}".encode()
    return hmac.new(settings.api_secret_key.encode(), msg, hashlib.sha256).hexdigest()[:32]


def _redirect_error(redirect_uri: str, state: str, error: str, desc: str = "") -> RedirectResponse:
    sep = "&" if "?" in redirect_uri else "?"
    url = f"{redirect_uri}{sep}error={quote(error)}&state={quote(state or '')}"
    if desc:
        url += f"&error_description={quote(desc)}"
    return RedirectResponse(url, status_code=302)


def _token_error(error: str, desc: str = "", status: int = 400) -> JSONResponse:
    body = {"error": error}
    if desc:
        body["error_description"] = desc
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


# Browser-rendered auth pages must never be framed — otherwise the consent
# "Erlauben" button is a clickjacking target. Defense in depth on top of the
# reverse proxy's global X-Frame-Options.
_FRAME_DENY = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}


def _html(content: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(content, status_code=status, headers=_FRAME_DENY)


# ---------------------------------------------------------------------------
# Discovery (RFC 8414 + RFC 9728)
# ---------------------------------------------------------------------------

@wellknown_router.get("/.well-known/oauth-authorization-server")
async def well_known_as():
    _require_enabled()
    return JSONResponse(oas.as_metadata())


@wellknown_router.get("/.well-known/oauth-protected-resource")
@wellknown_router.get("/.well-known/oauth-protected-resource/api/v1/mcp/msgraph")
async def well_known_prm():
    _require_enabled()
    return JSONResponse(oas.prm_metadata())


# ---------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# ---------------------------------------------------------------------------

@oauth_router.post("/register")
async def register(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    resp, err = await oas.register_client(db, body)
    if err:
        return JSONResponse({"error": "invalid_redirect_uri", "error_description": err}, status_code=400)
    return JSONResponse(resp, status_code=201)


# ---------------------------------------------------------------------------
# Authorization endpoint (consent over the existing platform session)
# ---------------------------------------------------------------------------

@oauth_router.get("/authorize")
async def authorize(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    q = request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")

    # Validate client + redirect BEFORE any redirect, so we never bounce to an
    # unvalidated URI (open-redirect / code-injection guard).
    client = await oas.get_client(db, client_id)
    if not client or redirect_uri not in oas.client_redirect_uris(client):
        return _html(_error_page("Ungültiger Client oder redirect_uri."), status=400)

    if q.get("response_type") != "code":
        return _redirect_error(redirect_uri, q.get("state", ""), "unsupported_response_type")
    code_challenge = q.get("code_challenge", "")
    if not code_challenge or q.get("code_challenge_method") != "S256":
        return _redirect_error(redirect_uri, q.get("state", ""), "invalid_request", "PKCE S256 required")

    user_id = _session_user_id(request)
    if not user_id:
        # Bounce through the platform login, then come back to this exact URL.
        back = quote(f"{request.url.path}?{request.url.query}", safe="")
        return RedirectResponse(f"{oas.issuer()}/login?redirect={back}", status_code=302)

    return _html(_consent_page(
        client_name=client.client_name or client_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        state=q.get("state", ""),
        scope=q.get("scope", oas.MCP_SCOPE),
        csrf=_csrf(user_id, client_id),
    ))


@oauth_router.post("/authorize")
async def authorize_decide(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    _require_enabled()
    form = await request.form()
    client_id = str(form.get("client_id", ""))
    redirect_uri = str(form.get("redirect_uri", ""))
    state = str(form.get("state", ""))

    client = await oas.get_client(db, client_id)
    if not client or redirect_uri not in oas.client_redirect_uris(client):
        return _html(_error_page("Ungültiger Client oder redirect_uri."), status=400)

    user_id = _session_user_id(request)
    if not user_id:
        return _redirect_error(redirect_uri, state, "access_denied", "not authenticated")
    if not hmac.compare_digest(str(form.get("csrf", "")), _csrf(user_id, client_id)):
        return _redirect_error(redirect_uri, state, "access_denied", "csrf")
    if form.get("decision") != "allow":
        return _redirect_error(redirect_uri, state, "access_denied")

    code = await oas.issue_code(redis, {
        "user_id": user_id,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": str(form.get("code_challenge", "")),
        "scope": str(form.get("scope", oas.MCP_SCOPE)),
    })
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={quote(code)}&state={quote(state)}", status_code=302)


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------

@oauth_router.post("/token")
async def token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    _require_enabled()
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = str(form.get("client_id", ""))

    client = await oas.get_client(db, client_id)
    if not client:
        return _token_error("invalid_client", status=401)
    if not oas.verify_client_secret(client, form.get("client_secret")):
        return _token_error("invalid_client", status=401)

    if grant_type == "authorization_code":
        data = await oas.consume_code(redis, str(form.get("code", "")))
        if not data or data["client_id"] != client_id or data["redirect_uri"] != str(form.get("redirect_uri", "")):
            return _token_error("invalid_grant")
        if not oas.verify_pkce(str(form.get("code_verifier", "")), data["code_challenge"]):
            return _token_error("invalid_grant", "PKCE verification failed")
        return await _issue_tokens(redis, data["user_id"], client_id, data.get("scope", oas.MCP_SCOPE))

    if grant_type == "refresh_token":
        data = await oas.consume_refresh(redis, str(form.get("refresh_token", "")))
        if not data or data["client_id"] != client_id:
            return _token_error("invalid_grant")
        return await _issue_tokens(redis, data["user_id"], client_id, data.get("scope", oas.MCP_SCOPE))

    return _token_error("unsupported_grant_type")


async def _issue_tokens(redis, user_id: str, client_id: str, scope: str) -> JSONResponse:
    access = oas.mint_access_token(user_id, client_id, scope)
    refresh = await oas.issue_refresh(redis, {"user_id": user_id, "client_id": client_id, "scope": scope})
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": oas.ACCESS_TTL_SECONDS,
            "refresh_token": refresh,
            "scope": scope,
        },
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Minimal HTML (consent + error)
# ---------------------------------------------------------------------------

_PAGE_CSS = (
    "body{background:#0a0a0f;color:#e5e7eb;font-family:Inter,system-ui,sans-serif;"
    "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
    ".card{background:#13131c;border:1px solid rgba(255,255,255,.08);border-radius:16px;"
    "padding:32px;max-width:420px;width:90%}"
    "h1{font-size:18px;margin:0 0 6px}p{color:#9ca3af;font-size:14px;line-height:1.5}"
    ".muted{color:#6b7280;font-size:12px;word-break:break-all}"
    ".row{display:flex;gap:10px;margin-top:20px}"
    "button{flex:1;padding:10px;border-radius:10px;border:0;font-size:14px;font-weight:600;cursor:pointer}"
    ".allow{background:#2563eb;color:#fff}.deny{background:rgba(255,255,255,.06);color:#e5e7eb}"
)


def _consent_page(*, client_name, client_id, redirect_uri, code_challenge, state, scope, csrf) -> str:
    import html
    cn = html.escape(client_name)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zugriff erlauben</title><style>{_PAGE_CSS}</style></head><body>
<div class="card">
  <h1>Microsoft 365 freigeben</h1>
  <p><b>{cn}</b> möchte in deinem Namen auf dein über AI-Employee verbundenes
     Microsoft 365 zugreifen (E-Mail, Kalender, Teams, To-Do, Dateien).</p>
  <p class="muted">Weiterleitung an: {html.escape(redirect_uri)}</p>
  <form method="post" action="/api/v1/oauth/authorize">
    <input type="hidden" name="client_id" value="{html.escape(client_id)}">
    <input type="hidden" name="redirect_uri" value="{html.escape(redirect_uri)}">
    <input type="hidden" name="code_challenge" value="{html.escape(code_challenge)}">
    <input type="hidden" name="state" value="{html.escape(state)}">
    <input type="hidden" name="scope" value="{html.escape(scope)}">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="row">
      <button class="deny" name="decision" value="deny" type="submit">Ablehnen</button>
      <button class="allow" name="decision" value="allow" type="submit">Erlauben</button>
    </div>
  </form>
</div></body></html>"""


def _error_page(message: str) -> str:
    import html
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Fehler</title><style>{_PAGE_CSS}</style></head><body>
<div class="card"><h1>Anfrage abgelehnt</h1><p>{html.escape(message)}</p></div>
</body></html>"""
