"""Built-in OAuth 2.1 Authorization Server for the external MS Graph MCP server.

Implements just enough of OAuth 2.1 to let an external MCP client (e.g. OpenWebUI)
log a user in and obtain a token that the MCP *resource server*
(``mcp_msgraph_external.py``) validates and maps to that user's stored Microsoft
account. Standards: OAuth 2.1 (authorization_code + PKCE S256, refresh_token),
RFC 7591 (Dynamic Client Registration), RFC 8414 (AS metadata), RFC 9728
(Protected Resource Metadata), RFC 8707 (resource indicators).

Design choices that keep this small and safe:
  * We do NOT re-authenticate the user here — the ``/oauth/authorize`` endpoint
    reuses the platform's existing session (httpOnly JWT cookie / Microsoft SSO).
  * Access tokens are short-lived signed JWTs (aud = the MCP resource URL).
  * Authorization codes (60s, single-use) and refresh tokens (30d, rotated) live
    in Redis — same store the rest of the OAuth machinery already uses.
  * Registered clients live in the ``oauth_clients`` table (durable across reboots).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timezone

import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import ALGORITHM, hash_password, verify_password
from app.models.oauth_client import OAuthClient

# Single coarse scope — the MCP server grants the user's own M365 surface.
MCP_SCOPE = "msgraph"
ACCESS_TTL_SECONDS = 3600          # 1h access tokens
CODE_TTL_SECONDS = 60              # authorization code lifetime (single-use)
REFRESH_TTL_SECONDS = 60 * 60 * 24 * 30  # 30d refresh tokens (rotated on use)
MAX_CLIENTS = 500                  # ceiling on DCR-registered clients (abuse guard)
TOKEN_USE = "mcp_msgraph"          # custom claim so these tokens can't be confused
                                   # with the platform's own session JWTs

_CODE_PREFIX = "mcp_oauth:code:"
_REFRESH_PREFIX = "mcp_oauth:refresh:"


# ---------------------------------------------------------------------------
# Issuer / resource identity
# ---------------------------------------------------------------------------

def issuer() -> str:
    """Public base URL of this authorization server (no trailing slash)."""
    return settings.oauth_redirect_base_url.rstrip("/")


def resource_url() -> str:
    """Canonical identifier (audience) of the protected MCP resource."""
    return f"{issuer()}/api/v1/mcp/msgraph"


def authorization_endpoint() -> str:
    return f"{issuer()}/api/v1/oauth/authorize"


def token_endpoint() -> str:
    return f"{issuer()}/api/v1/oauth/token"


def registration_endpoint() -> str:
    return f"{issuer()}/api/v1/oauth/register"


def prm_url() -> str:
    return f"{issuer()}/.well-known/oauth-protected-resource"


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------

def as_metadata() -> dict:
    """RFC 8414 Authorization Server Metadata."""
    return {
        "issuer": issuer(),
        "authorization_endpoint": authorization_endpoint(),
        "token_endpoint": token_endpoint(),
        "registration_endpoint": registration_endpoint(),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": [MCP_SCOPE],
    }


def prm_metadata() -> dict:
    """RFC 9728 Protected Resource Metadata."""
    return {
        "resource": resource_url(),
        "authorization_servers": [issuer()],
        "scopes_supported": [MCP_SCOPE],
        "bearer_methods_supported": ["header"],
    }


# ---------------------------------------------------------------------------
# PKCE (RFC 7636, S256 only)
# ---------------------------------------------------------------------------

def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    # RFC 7636: verifier is 43-128 chars of high entropy. Reject anything outside
    # that range so a trivially short verifier can't make PKCE meaningless.
    if not code_verifier or not (43 <= len(code_verifier) <= 128):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(expected, code_challenge or "")


# ---------------------------------------------------------------------------
# Access tokens (signed JWT, audience-bound to the MCP resource)
# ---------------------------------------------------------------------------

def _mcp_key() -> str:
    """Signing key for MCP access tokens — domain-separated from the platform
    session key so the two token families don't share signing bytes. Set an
    independent ``MCP_SIGNING_KEY`` for full key isolation; otherwise this derives
    deterministically from ``api_secret_key`` via HMAC."""
    explicit = getattr(settings, "mcp_signing_key", "") or ""
    if explicit:
        return explicit
    return hmac.new(settings.api_secret_key.encode(), b"mcp-msgraph-oauth-v1", hashlib.sha256).hexdigest()


def mint_access_token(user_id: str, client_id: str, scope: str = MCP_SCOPE) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": issuer(),
        "sub": user_id,
        "aud": resource_url(),
        "client_id": client_id,
        "scope": scope,
        "token_use": TOKEN_USE,
        "iat": now,
        "exp": now + _td(ACCESS_TTL_SECONDS),
        "jti": uuid.uuid4().hex[:16],
    }
    return jwt.encode(payload, _mcp_key(), algorithm=ALGORITHM)


def verify_access_token(token: str) -> str:
    """Validate an MCP access token and return the user_id. Raises on any failure."""
    data = jwt.decode(
        token,
        _mcp_key(),
        algorithms=[ALGORITHM],
        audience=resource_url(),
        options={"require": ["exp", "sub", "aud"]},
    )
    if data.get("token_use") != TOKEN_USE:
        raise jwt.InvalidTokenError("wrong token_use")
    return data["sub"]


def _td(seconds: int):
    from datetime import timedelta
    return timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Authorization codes (Redis, single-use)
# ---------------------------------------------------------------------------

async def issue_code(redis, payload: dict) -> str:
    code = secrets.token_urlsafe(32)
    await redis.client.setex(_CODE_PREFIX + code, CODE_TTL_SECONDS, json.dumps(payload))
    return code


async def consume_code(redis, code: str) -> dict | None:
    key = _CODE_PREFIX + (code or "")
    raw = await redis.client.get(key)
    if raw is None:
        return None
    await redis.client.delete(key)  # single-use
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Refresh tokens (Redis, rotated on use)
# ---------------------------------------------------------------------------

async def issue_refresh(redis, payload: dict) -> str:
    tok = secrets.token_urlsafe(40)
    await redis.client.setex(_REFRESH_PREFIX + tok, REFRESH_TTL_SECONDS, json.dumps(payload))
    return tok


async def consume_refresh(redis, tok: str) -> dict | None:
    key = _REFRESH_PREFIX + (tok or "")
    raw = await redis.client.get(key)
    if raw is None:
        return None
    await redis.client.delete(key)  # rotation: old token is invalidated
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Client registration (RFC 7591) + lookup
# ---------------------------------------------------------------------------

def _valid_redirect_uri(uri: str) -> bool:
    """Allow https everywhere; http only for loopback (native/desktop clients)."""
    if not isinstance(uri, str) or len(uri) > 2000:
        return False
    if uri.startswith("https://"):
        return True
    if uri.startswith("http://"):
        host = uri[len("http://"):].split("/", 1)[0].split(":", 1)[0]
        return host in ("localhost", "127.0.0.1", "[::1]")
    return False


async def register_client(db: AsyncSession, body: dict) -> tuple[dict, str | None]:
    """Create a client from an RFC 7591 registration request.

    Returns ``(response_dict, error)``. On success ``error`` is None and the dict
    is the RFC 7591 client-information response (includes ``client_secret`` once
    for confidential clients).
    """
    # Hard ceiling on registered clients — /oauth/register is unauthenticated (per
    # RFC 7591), so cap total rows to prevent DB/disk exhaustion abuse.
    total = (await db.execute(select(func.count()).select_from(OAuthClient))).scalar() or 0
    if total >= MAX_CLIENTS:
        return {}, "client registration limit reached"

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return {}, "redirect_uris is required"
    if not all(_valid_redirect_uri(u) for u in redirect_uris):
        return {}, "one or more redirect_uris are invalid (https required; http only for loopback)"

    auth_method = body.get("token_endpoint_auth_method", "none")
    if auth_method not in ("none", "client_secret_post", "client_secret_basic"):
        auth_method = "none"
    grant_types = body.get("grant_types") or ["authorization_code", "refresh_token"]
    grant_types = [g for g in grant_types if g in ("authorization_code", "refresh_token")]
    if "authorization_code" not in grant_types:
        grant_types.append("authorization_code")

    client_id = "mcp_" + secrets.token_urlsafe(18)
    secret_plain = None
    secret_hash = None
    if auth_method != "none":
        secret_plain = secrets.token_urlsafe(32)
        secret_hash = hash_password(secret_plain)

    client = OAuthClient(
        client_id=client_id,
        client_secret_hash=secret_hash,
        client_name=str(body.get("client_name") or "")[:255] or None,
        redirect_uris=json.dumps(redirect_uris),
        grant_types=" ".join(grant_types),
        token_endpoint_auth_method=auth_method,
        scope=MCP_SCOPE,
    )
    db.add(client)
    await db.commit()

    resp = {
        "client_id": client_id,
        "client_id_issued_at": int(datetime.now(timezone.utc).timestamp()),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "token_endpoint_auth_method": auth_method,
        "scope": MCP_SCOPE,
    }
    if client.client_name:
        resp["client_name"] = client.client_name
    if secret_plain:
        resp["client_secret"] = secret_plain
        resp["client_secret_expires_at"] = 0  # never expires
    return resp, None


async def get_client(db: AsyncSession, client_id: str) -> OAuthClient | None:
    if not client_id:
        return None
    return (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    ).scalar_one_or_none()


def client_redirect_uris(client: OAuthClient) -> list[str]:
    try:
        return json.loads(client.redirect_uris or "[]")
    except Exception:
        return []


def verify_client_secret(client: OAuthClient, secret: str | None) -> bool:
    """Confidential clients must present the matching secret; public clients pass."""
    if client.token_endpoint_auth_method == "none" or not client.client_secret_hash:
        return True
    if not secret:
        return False
    return verify_password(secret, client.client_secret_hash)
