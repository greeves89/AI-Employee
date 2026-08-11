"""API endpoints for managing external MCP servers."""

import asyncio
import json as json_mod
import os
import re
import secrets
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import quote, urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies import get_redis_service, require_admin, require_auth
from app.models.audit_log import AuditEventType, AuditLog
from app.models.mcp_server import McpServer
from app.services.redis_service import RedisService

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])

MCP_HEALTH_OK = "ok"
MCP_HEALTH_AUTH_FAILED = "auth_failed"
MCP_HEALTH_UNREACHABLE = "unreachable"
MCP_HEALTH_PROTOCOL_ERROR = "protocol_error"
# The server answered 401 with an RFC 9728 OAuth challenge: not an error, it just
# still needs the Connect flow. Distinguished from auth_failed (a rejected static
# token) so the UI can steer the operator to "Verbinden" instead of showing red.
MCP_HEALTH_NEEDS_OAUTH = "needs_oauth"
MCP_HEALTH_STATUSES = {
    MCP_HEALTH_OK,
    MCP_HEALTH_AUTH_FAILED,
    MCP_HEALTH_UNREACHABLE,
    MCP_HEALTH_PROTOCOL_ERROR,
    MCP_HEALTH_NEEDS_OAUTH,
}


@dataclass
class McpDiscoveryError(Exception):
    status: str
    message: str

    def __str__(self) -> str:
        return self.message


def _sanitize_mcp_name(name: str) -> str:
    """Sanitize MCP server name: only letters, numbers, hyphens, underscores."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")


def _mcp_allow_private() -> bool:
    """Whether MCP server URLs may point at private/loopback hosts.

    Off by default (secure): the URL is operator-supplied infrastructure, but a
    typo or a compromised admin session must not turn this into an SSRF pivot to
    the DB, Redis or the cloud metadata endpoint. Deployments whose MCP servers
    genuinely live on the internal docker network set MCP_ALLOW_PRIVATE_URLS=true.
    """
    return os.getenv("MCP_ALLOW_PRIVATE_URLS", "").strip().lower() in ("1", "true", "yes")


def _forbidden_ip_reason(ip, *, allow_private: bool = False) -> str | None:
    """Return a rejection reason if ``ip`` is an SSRF-forbidden address, else None.

    Drei Stufen, und der Unterschied ist wichtig:

    * **Nie erlaubt** — link-local (darunter der Cloud-Metadatenpunkt
      169.254.169.254), Multicast, reserviert, unbestimmt. Dort steht nie ein
      MCP-Server, dort steht Infrastruktur.
    * **Loopback** — bleibt gesperrt, auch mit ``allow_private``. Innerhalb des
      Containers ist ``127.0.0.1`` der Orchestrator selbst; ein interner
      MCP-Server steht dort nicht, die eigene API schon. Nur der globale
      Schalter ``MCP_ALLOW_PRIVATE_URLS`` hebt das auf — er hatte diese
      Bedeutung schon, und Bestehendes soll sich nicht ändern.
    * **Privat** (10./172.16./192.168.) — genau der Fall „unser eigener Server
      im Haus". Den darf ein Administrator pro Eintrag zulassen, statt den
      globalen Schalter für die ganze Installation umzulegen.
    """
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return f"MCP host resolves to a forbidden address ({ip})"
    if ip.is_loopback and not _mcp_allow_private():
        return (
            f"MCP host resolves to a loopback address ({ip}) — that is this "
            "server itself, not an MCP server"
        )
    if ip.is_private and not (allow_private or _mcp_allow_private()):
        return (
            f"MCP host resolves to a private address ({ip}); "
            'Haken \u201einterne Adresse zulassen\u201c setzen, wenn der Server '
            "wirklich im eigenen Netz steht"
        )
    return None


async def _resolve_host_ips(hostname: str, port: int) -> list:
    """Resolve ``hostname`` to a list of ip_address objects (may raise socket.gaierror)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    return [ip_address(info[4][0]) for info in infos]


async def _assert_mcp_url_allowed(url: str, *, allow_private: bool = False) -> None:
    """SSRF guard for a manual ``tools/call`` invocation. Raises HTTPException(400) if blocked.

    Resolves the hostname and checks every returned address (so a name that
    resolves to one public and one internal IP is still rejected), unlike
    :func:`_validate_mcp_url` which only catches IP-literal hosts. Fail-closed: an
    unresolvable host is rejected, since a manual call must not proceed on an
    unverifiable target.

    TOCTOU note (#441): the guard resolves DNS here at request time, but httpx
    re-resolves at connect time, so a name that flips its A-record between the two
    lookups (DNS rebinding) could still reach an internal address. This is an
    admin-only endpoint, so the gap is an accepted risk. To close it, pin the
    resolved IPs by connecting through a custom httpx transport that reuses the
    addresses validated here instead of re-resolving. Keep this comment.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="MCP server URL must be a valid http(s) URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        ips = await _resolve_host_ips(parsed.hostname, port)
    except socket.gaierror as e:
        raise HTTPException(status_code=400, detail=f"Cannot resolve MCP host: {parsed.hostname}") from e
    if not ips:
        raise HTTPException(status_code=400, detail=f"Cannot resolve MCP host: {parsed.hostname}")
    for ip in ips:
        reason = _forbidden_ip_reason(ip, allow_private=allow_private)
        if reason:
            raise HTTPException(status_code=400, detail=reason)


async def _assert_discovery_host_allowed(url: str, *, allow_private: bool = False) -> None:
    """DNS-resolving SSRF guard for the add/refresh/probe discovery path.

    Raises :class:`McpDiscoveryError` (so the failure is health-classified like every
    other discovery error) if the host resolves to a forbidden/private address. This
    closes the gap where :func:`_validate_mcp_url` only blocks IP *literals*, letting a
    name like ``http://redis/`` through. Fail-open on resolution failure: an
    unresolvable host carries no SSRF risk and is left to the httpx layer, which
    classifies it as UNREACHABLE. Scheme/IP-literal checks are already done by
    :func:`_validate_mcp_url`, which callers run first.
    """
    parsed = urlparse((url or "").strip())
    if not parsed.hostname:
        return
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        ips = await _resolve_host_ips(parsed.hostname, port)
    except socket.gaierror:
        return
    for ip in ips:
        reason = _forbidden_ip_reason(ip, allow_private=allow_private)
        if reason:
            raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, reason)


class McpServerCreate(BaseModel):
    name: str
    url: str
    #: Private Adresse fuer DIESEN Server zulassen. Nur Administratoren erreichen
    #: diese Endpunkte ueberhaupt; der Haken haelt fest, dass die interne Adresse
    #: Absicht war und kein Vertipper.
    allow_private_host: bool = False
    bearer_token: str | None = None  # plaintext on input; stored Fernet-encrypted
    # Custom auth headers {name: value} for servers expecting a non-Bearer key.
    headers: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        return _sanitize_mcp_name(v)


class McpServerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    bearer_token: str | None = None  # "" clears the token; None leaves it unchanged
    headers: dict[str, str] | None = None  # {} clears; None leaves unchanged


class McpToolCall(BaseModel):
    name: str
    arguments: dict = {}


async def _write_audit(
    db: AsyncSession, event_type: AuditEventType, command: str,
    outcome: str, user_id: str, meta: dict | None = None,
) -> None:
    """Persist one MCP audit row. Never raises — auditing must not break the request."""
    try:
        db.add(AuditLog(
            agent_id="admin",
            event_type=event_type,
            command=command,
            outcome=outcome,
            user_id=user_id,
            meta=meta,
        ))
        await db.commit()
    except Exception:
        await db.rollback()


def _audit_discovery_failure(db: AsyncSession, command: str, user_id: str, meta: dict) -> None:
    """Stage an MCP_DISCOVERY_FAILED row without committing.

    Used on the add/refresh/probe failure paths, which already have their own
    single commit right after (for refresh, that commit also persists the
    health-state fields set by ``_mark_health``). Staging here instead of
    committing separately keeps that call to exactly one commit.
    """
    db.add(AuditLog(
        agent_id="admin",
        event_type=AuditEventType.MCP_DISCOVERY_FAILED,
        command=command,
        outcome="failure",
        user_id=user_id,
        meta=meta,
    ))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _short_error(text: str | None) -> str | None:
    if not text:
        return None
    compact = " ".join(str(text).split())
    return compact[:252] + "..." if len(compact) > 255 else compact


def _status_from_http(status_code: int) -> str:
    return MCP_HEALTH_AUTH_FAILED if status_code == 401 else MCP_HEALTH_PROTOCOL_ERROR


def _response_error(resp: httpx.Response, phase: str) -> McpDiscoveryError:
    reason = resp.reason_phrase or "HTTP error"
    return McpDiscoveryError(
        _status_from_http(resp.status_code),
        _short_error(f"{resp.status_code} {reason} on {phase}") or "MCP server request failed",
    )


def _serialize_mcp_server(server: McpServer) -> dict:
    return {
        "id": server.id,
        "name": server.name,
        "url": server.url,
        "tools": server.tools or [],
        "enabled": server.enabled,
        "has_auth": bool(server.auth_token_encrypted),
        "has_headers": bool(server.headers_encrypted),
        "created_at": server.created_at.isoformat() if server.created_at else None,
        "last_checked_at": server.last_checked_at.isoformat() if server.last_checked_at else None,
        "last_status": server.last_status,
        "last_error": server.last_error,
        "allow_private_host": bool(getattr(server, "allow_private_host", False)),
        "oauth_enabled": bool(getattr(server, "oauth_enabled", False)),
        "oauth_client_id": getattr(server, "oauth_client_id", None),
        "oauth_connected": bool(getattr(server, "oauth_refresh_token_encrypted", None)),
        "oauth_scope": getattr(server, "oauth_scope", None),
        "oauth_expires_at": (
            server.oauth_access_expires_at.isoformat()
            if getattr(server, "oauth_access_expires_at", None) else None
        ),
    }


def _mark_health(server: McpServer, status: str, error: str | None = None) -> None:
    if status not in MCP_HEALTH_STATUSES:
        status = MCP_HEALTH_PROTOCOL_ERROR
    server.last_checked_at = _now_utc()
    server.last_status = status
    server.last_error = _short_error(error)


def _validate_mcp_url(url: str, *, allow_private: bool = False) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL") from exc
    if port is not None and not (1 <= port <= 65535):
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid MCP server URL")

    host = parsed.hostname.strip("[]").lower()
    if host in {"localhost", "metadata.google.internal"}:
        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "MCP server URL host is not allowed")
    try:
        ip = ip_address(host)
    except ValueError:
        pass
    else:
        reason = _forbidden_ip_reason(ip, allow_private=allow_private)
        if reason:
            raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, reason)

    return urlunparse(parsed._replace(fragment=""))


def _parse_jsonrpc_response(resp: httpx.Response) -> dict | None:
    """Parse a JSON-RPC response that may be JSON or SSE (text/event-stream)."""
    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        # Parse SSE: look for "data: " lines containing JSON
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    return json_mod.loads(line[6:])
                except json_mod.JSONDecodeError:
                    continue
        return None

    # application/json or other - try direct JSON parse
    try:
        return resp.json()
    except Exception:
        return None


def _build_headers(
    bearer_token: str | None, extra_headers: dict[str, str] | None,
) -> dict[str, str]:
    """Build the Streamable-HTTP request headers, merging Bearer + custom auth."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if extra_headers:
        headers.update({str(k): str(v) for k, v in extra_headers.items() if k})
    return headers


async def _initialize_session(
    client: httpx.AsyncClient, url: str, headers: dict[str, str],
) -> dict[str, str]:
    """Run the MCP ``initialize`` handshake + ``initialized`` notification for a
    manual ``tools/call`` (see :func:`_call_tool`).

    Returns the headers to use for subsequent requests (carrying the
    ``mcp-session-id`` for stateful servers). Raises ``HTTPException(400)`` with
    the real cause in ``detail`` if the server rejects the handshake — a 502
    would be swallowed by a fronting Cloudflare tunnel, hiding the reason.

    :func:`_discover_tools` runs its own copy of this handshake inline because it
    needs to classify failures into health states (``McpDiscoveryError``) rather
    than raise a flat ``HTTPException``; a manual tool call is not tracked in
    server health, so the simpler exception here is sufficient.
    """
    init_resp = await client.post(url, headers=headers, json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-employee-orchestrator", "version": "1.0.0"},
        },
    })

    if init_resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"MCP server returned {init_resp.status_code} on initialize "
                   "(check URL and auth token/headers)",
        )

    init_data = _parse_jsonrpc_response(init_resp)
    if not init_data or "result" not in init_data:
        raise HTTPException(
            status_code=400,
            detail="MCP server returned an invalid initialize response",
        )

    # Extract session ID from response header if present (for stateful servers)
    session_id = init_resp.headers.get("mcp-session-id")
    tool_headers = {**headers}
    if session_id:
        tool_headers["mcp-session-id"] = session_id

    # Send initialized notification
    await client.post(url, headers=tool_headers, json={
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })
    return tool_headers


async def _discover_tools(
    url: str,
    bearer_token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    *,
    allow_private: bool = False,
) -> list[dict]:
    """Connect to an MCP server via Streamable HTTP and list its tools.

    Handles both application/json and text/event-stream (SSE) responses,
    as servers like n8n respond with SSE format. An optional Bearer token is
    sent as ``Authorization: Bearer <token>``; ``extra_headers`` (e.g. an
    ``x-api-key``) are merged on top so non-Bearer servers can authenticate.
    """
    safe_url = _validate_mcp_url(url, allow_private=allow_private)
    # ACHTUNG: hier stand der Aufruf OHNE ``allow_private`` — und damit wirkte der
    # Haken nur bei IP-Adressen in der URL, nicht bei Namen, die sich auf eine
    # private Adresse aufloesen. Also ausgerechnet nicht im gedachten Fall
    # (ein Hausname wie ``mcp.intern.example`` oder ein Docker-Containername).
    await _assert_discovery_host_allowed(safe_url, allow_private=allow_private)
    headers = _build_headers(bearer_token, extra_headers)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: Initialize
        try:
            init_resp = await client.post(safe_url, headers=headers, json={  # codeql[py/full-ssrf]: URL is validated by _validate_mcp_url above.
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ai-employee-orchestrator", "version": "1.0.0"},
                },
            })
        except httpx.RequestError as exc:
            raise McpDiscoveryError(
                MCP_HEALTH_UNREACHABLE,
                "Connection failed during initialize",
            ) from exc

        if init_resp.status_code != 200:
            raise _response_error(init_resp, "initialize")

        init_data = _parse_jsonrpc_response(init_resp)
        if not init_data or "result" not in init_data:
            raise McpDiscoveryError(
                MCP_HEALTH_PROTOCOL_ERROR,
                "Invalid initialize response",
            )

        # Extract session ID from response header if present (for stateful servers)
        session_id = init_resp.headers.get("mcp-session-id")
        tool_headers = {**headers}
        if session_id:
            tool_headers["mcp-session-id"] = session_id

        # Send initialized notification
        try:
            await client.post(safe_url, headers=tool_headers, json={  # codeql[py/full-ssrf]: URL is validated by _validate_mcp_url above.
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })
        except httpx.RequestError as exc:
            raise McpDiscoveryError(
                MCP_HEALTH_UNREACHABLE,
                "Connection failed during initialized notification",
            ) from exc

        # Step 2: List tools
        try:
            tools_resp = await client.post(safe_url, headers=tool_headers, json={  # codeql[py/full-ssrf]: URL is validated by _validate_mcp_url above.
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })
        except httpx.RequestError as exc:
            raise McpDiscoveryError(
                MCP_HEALTH_UNREACHABLE,
                "Connection failed during tools/list",
            ) from exc

        if tools_resp.status_code != 200:
            raise _response_error(tools_resp, "tools/list")

        data = _parse_jsonrpc_response(tools_resp)

        if isinstance(data, dict) and "result" in data:
            return data["result"].get("tools", [])
        elif isinstance(data, list):
            # Batch response
            for item in data:
                if isinstance(item, dict) and item.get("id") == 2 and "result" in item:
                    return item["result"].get("tools", [])

        raise McpDiscoveryError(MCP_HEALTH_PROTOCOL_ERROR, "Invalid tools/list response")


async def _call_tool(
    url: str,
    tool_name: str,
    arguments: dict,
    bearer_token: str | None = None,
    extra_headers: dict[str, str] | None = None,
    *,
    allow_private: bool = False,
) -> dict:
    """Invoke a single tool (``tools/call``) and return the raw JSON-RPC object.

    Reuses the same handshake as :func:`_discover_tools`. The returned dict is the
    server's response verbatim — including a JSON-RPC ``error`` member if the tool
    itself failed — so the operator sees exactly what the server said. Transport
    failures raise ``HTTPException(400)`` with the real cause in ``detail``.
    """
    # Derselbe Server, dieselbe Entscheidung: wer eingetragen werden durfte, muss
    # auch aufrufbar sein. Sonst laesst sich ein interner Server hinzufuegen, aber
    # seine Werkzeuge nicht ausprobieren.
    await _assert_mcp_url_allowed(url, allow_private=allow_private)
    headers = _build_headers(bearer_token, extra_headers)

    async with httpx.AsyncClient(timeout=30.0) as client:
        tool_headers = await _initialize_session(client, url, headers)

        call_resp = await client.post(url, headers=tool_headers, json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments or {}},
        })

        if call_resp.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"MCP server returned {call_resp.status_code} on tools/call",
            )

        data = _parse_jsonrpc_response(call_resp)
        if isinstance(data, list):
            # Batch response — pick the entry matching our request id
            for item in data:
                if isinstance(item, dict) and item.get("id") == 3:
                    return item
        if isinstance(data, dict):
            return data
        raise HTTPException(
            status_code=400,
            detail="MCP server returned an unparseable tools/call response",
        )


@router.get("")
async def list_mcp_servers(user=Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """List all registered MCP servers."""
    result = await db.execute(select(McpServer).order_by(McpServer.created_at.desc()))
    servers = result.scalars().all()
    return {
        "servers": [
            _serialize_mcp_server(s)
            for s in servers
        ]
    }


async def _advertises_oauth(url: str) -> bool:
    """True when the server answers the initialize probe with an RFC 9728 OAuth
    challenge (``WWW-Authenticate: Bearer resource_metadata="…"``).

    Used to tell an OAuth-protected server (which SHOULD be created so the Connect
    flow becomes reachable) apart from a genuinely rejected static token. Best
    effort: any probe failure returns False so we fall back to the normal abort.
    """
    from app.services import mcp_oauth_client as oc
    try:
        www_auth = await _oauth_probe_challenge(url)
    except HTTPException:
        return False
    # Only the challenge's own ``resource_metadata`` pointer counts. Passing the
    # server URL as a fallback would derive a well-known path for ANY https host,
    # so a plain rejected static token (401 with no OAuth challenge) would be
    # misread as OAuth. RFC 9728 requires the pointer to be advertised explicitly.
    return bool(www_auth and oc.resource_metadata_url(www_auth))


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_mcp_server(body: McpServerCreate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Register a new MCP server and discover its tools."""
    # Check for duplicate name
    existing = await db.execute(select(McpServer).where(McpServer.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"MCP server '{body.name}' already exists")

    from app.core.encryption import encrypt_token

    # Discover tools
    try:
        tools = await _discover_tools(body.url, body.bearer_token, body.headers,
                                      allow_private=body.allow_private_host)
    except McpDiscoveryError as e:
        # A 401 that carries an OAuth challenge is not a failure: the server is
        # OAuth-protected and simply needs the Connect flow, which can only be
        # started against a server that already exists (issue #465). Create the
        # row in a needs_oauth state instead of aborting, so the "OAuth offen"
        # badge and "Verbinden" button become reachable. Any static creds the
        # caller supplied are irrelevant to OAuth, so they are not stored.
        if e.status == MCP_HEALTH_AUTH_FAILED and await _advertises_oauth(body.url):
            server = McpServer(
                name=body.name, url=body.url, tools=[], enabled=True,
                oauth_enabled=True,
                allow_private_host=body.allow_private_host,
            )
            _mark_health(server, MCP_HEALTH_NEEDS_OAUTH,
                         "OAuth erforderlich — auf 'Verbinden' klicken, um die Autorisierung zu starten")
            db.add(server)
            await db.commit()
            await db.refresh(server)
            return {**_serialize_mcp_server(server), "needs_oauth": True}

        _audit_discovery_failure(db, f"add:{body.name}", str(user.id),
                                  {"url": body.url, "detail": e.message})
        await db.commit()
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        _audit_discovery_failure(db, f"add:{body.name}", str(user.id),
                                  {"url": body.url, "detail": _short_error(str(e))})
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect to MCP server: {_short_error(str(e))}")

    server = McpServer(
        name=body.name, url=body.url, tools=tools, enabled=True,
        oauth_enabled=False,
        allow_private_host=body.allow_private_host,
        auth_token_encrypted=encrypt_token(body.bearer_token) if body.bearer_token else None,
        headers_encrypted=encrypt_token(json_mod.dumps(body.headers)) if body.headers else None,
    )
    _mark_health(server, MCP_HEALTH_OK)
    db.add(server)
    await db.commit()
    await db.refresh(server)

    return _serialize_mcp_server(server)


@router.post("/{server_id}/refresh")
async def refresh_mcp_tools(server_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Re-discover tools from an MCP server."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    from app.core.encryption import decrypt_token
    token = decrypt_token(server.auth_token_encrypted) if server.auth_token_encrypted else None
    extra = json_mod.loads(decrypt_token(server.headers_encrypted)) if server.headers_encrypted else None
    try:
        tools = await _discover_tools(server.url, token, extra,
                                      allow_private=bool(server.allow_private_host))
    except McpDiscoveryError as e:
        _mark_health(server, e.status, e.message)
        _audit_discovery_failure(db, f"refresh:{server.name}", str(user.id),
                                  {"server_id": server.id, "detail": e.message})
        await db.commit()
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        _mark_health(server, MCP_HEALTH_PROTOCOL_ERROR, "Unexpected discovery error")
        _audit_discovery_failure(db, f"refresh:{server.name}", str(user.id),
                                  {"server_id": server.id, "detail": _short_error(str(e))})
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect: {_short_error(str(e))}")

    server.tools = tools
    _mark_health(server, MCP_HEALTH_OK)
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(server, "tools")
    await db.commit()

    return _serialize_mcp_server(server)


@router.patch("/{server_id}")
async def update_mcp_server(
    server_id: int, body: McpServerUpdate, user=Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Update an MCP server's config."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    if body.name is not None:
        server.name = body.name
    if body.url is not None:
        server.url = body.url
    if body.enabled is not None:
        server.enabled = body.enabled
    if body.bearer_token is not None:
        from app.core.encryption import encrypt_token
        server.auth_token_encrypted = encrypt_token(body.bearer_token) if body.bearer_token.strip() else None
    if body.headers is not None:
        from app.core.encryption import encrypt_token
        server.headers_encrypted = encrypt_token(json_mod.dumps(body.headers)) if body.headers else None

    await db.commit()
    return _serialize_mcp_server(server)


@router.delete("/{server_id}")
async def delete_mcp_server(server_id: int, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Remove an MCP server."""
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    await db.delete(server)
    await db.commit()
    return {"deleted": True}


@router.post("/probe")
async def probe_mcp_server(body: McpServerCreate, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Probe an MCP server URL without saving it. Returns discovered tools."""
    try:
        # Pass the submitted bearer token AND custom headers so a probe against a
        # protected server actually authenticates (previously both were dropped →
        # a correctly-configured server always failed the connection test).
        tools = await _discover_tools(body.url, body.bearer_token, body.headers,
                                      allow_private=body.allow_private_host)
    except McpDiscoveryError as e:
        _audit_discovery_failure(db, f"probe:{body.name}", str(user.id),
                                  {"url": body.url, "detail": e.message})
        await db.commit()
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        _audit_discovery_failure(db, f"probe:{body.name}", str(user.id),
                                  {"url": body.url, "detail": _short_error(str(e))})
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect to MCP server: {_short_error(str(e))}")

    return {
        "url": body.url,
        "tools": tools,
        "tool_count": len(tools),
        "last_checked_at": _now_utc().isoformat(),
        "last_status": MCP_HEALTH_OK,
        "last_error": None,
    }


@router.get("/agent-health")
async def mcp_agent_health(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Agent-side MCP connection health (#425 Phase 2).

    Runs ``claude mcp list`` inside every running agent container and reports,
    per registered external server, how the agents actually see it — a signal
    independent of the orchestrator's own discovery check (``last_status``). This
    is what catches the case a green orchestrator status hides: e.g. a per-agent
    token that a server rejects with 401 on a URL the orchestrator reaches
    anonymously. Admin-only and on-demand (each check does live connectivity
    probes across N agents, so it is not run on every page load).
    """
    from app.models.agent import Agent, AgentState
    from app.services.docker_service import DockerService
    from app.services.mcp_agent_health import collect_agent_mcp_health

    servers_result = await db.execute(select(McpServer))
    name_to_id = {_sanitize_mcp_name(s.name): s.id for s in servers_result.scalars().all()}

    agents_result = await db.execute(
        select(Agent).where(
            Agent.container_id.is_not(None),
            Agent.state.in_([AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING]),
        )
    )
    agents = list(agents_result.scalars().all())

    docker = DockerService()

    async def _exec_list(container_id: str) -> str | None:
        def _run() -> str | None:
            try:
                _code, out = docker.exec_in_container(container_id, ["claude", "mcp", "list"])
                return out
            except Exception:
                return None

        return await asyncio.to_thread(_run)

    health = await collect_agent_mcp_health(agents, _exec_list, name_to_id.keys())

    servers_by_id = {
        str(name_to_id[name]): {"name": name, **data}
        for name, data in health["servers"].items()
        if name in name_to_id
    }
    return {
        "agents_checked": health["agents_checked"],
        "agents_total": len(agents),
        "servers": servers_by_id,
    }


@router.post("/{server_id}/call")
async def call_mcp_tool(
    server_id: int, body: McpToolCall, user=Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Invoke a single tool on a saved MCP server by hand and record the attempt.

    Diagnostic plumbing for operators (#414): a successful ``tools/call`` against a
    real server settles in one step whether URL + credential + connection state all
    line up, and a persisted audit row turns "it broke yesterday" into something
    answerable. Admin-only, like every other route here. The raw JSON-RPC result is
    returned verbatim (including a JSON-RPC ``error`` member if the tool failed).
    """
    result = await db.execute(select(McpServer).where(McpServer.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    from app.core.encryption import decrypt_token
    token = decrypt_token(server.auth_token_encrypted) if server.auth_token_encrypted else None
    extra = json_mod.loads(decrypt_token(server.headers_encrypted)) if server.headers_encrypted else None

    try:
        rpc = await _call_tool(server.url, body.name, body.arguments, token, extra,
                               allow_private=bool(server.allow_private_host))
    except HTTPException as e:
        # Transport / handshake failure — the call never ran on the server.
        await _write_audit(db, AuditEventType.MCP_TOOL_CALL_FAILED, f"{server.name}:{body.name}",
                           "failure", str(user.id), {"server_id": server.id, "tool": body.name,
                                                      "detail": str(e.detail)})
        raise
    except Exception as e:
        await _write_audit(db, AuditEventType.MCP_TOOL_CALL_FAILED, f"{server.name}:{body.name}",
                           "failure", str(user.id), {"server_id": server.id, "tool": body.name,
                                                      "detail": str(e)})
        raise HTTPException(status_code=400, detail=f"Could not call tool: {e}")

    # A well-formed response can still carry a JSON-RPC error (the tool itself failed).
    is_error = isinstance(rpc, dict) and "error" in rpc
    # Do NOT persist arguments (may contain secrets) — only server + tool + outcome.
    await _write_audit(db, AuditEventType.MCP_TOOL_CALLED, f"{server.name}:{body.name}",
                       "failure" if is_error else "success", str(user.id),
                       {"server_id": server.id, "tool": body.name})

    return {"server_id": server.id, "tool": body.name, "result": rpc, "is_error": is_error}


# ===========================================================================
# Client-side OAuth for OAuth-protected MCP servers (#426)
# ===========================================================================
#
# The orchestrator drives authorization_code + PKCE against the MCP server's own
# authorization server, stores the refresh token, and keeps a fresh access token
# in `auth_token_encrypted` (which already flows to agents). The flow runs in the
# operator's browser against the orchestrator's public URL — never inside an agent
# container, whose localhost callback would never arrive.

_STATE_PREFIX = "mcp_oauth_client:state:"
_STATE_TTL_SECONDS = 600  # 10 min to complete the browser round-trip


def _callback_redirect_uri() -> str:
    """Public redirect URI the authorization server sends the browser back to."""
    from app.core import mcp_oauth as oas
    return f"{oas.issuer()}/api/v1/mcp-servers/oauth/callback"


def _integrations_redirect(status: str, **params) -> RedirectResponse:
    """Bounce the browser back to the integrations page with a result marker."""
    from app.core import mcp_oauth as oas
    query = "&".join([f"mcp_oauth={quote(status)}"] + [f"{k}={quote(str(v))}" for k, v in params.items()])
    return RedirectResponse(f"{oas.issuer()}/integrations?{query}", status_code=302)


async def _oauth_fetch_json(url: str) -> dict:
    """SSRF-guarded GET of an OAuth discovery document (PRM / AS metadata)."""
    await _assert_mcp_url_allowed(url)
    async with httpx.AsyncClient(timeout=15.0) as client:
        # follow_redirects MUST stay off: the allowlist above is checked against
        # the URL we were given, so a redirect would move the request to an
        # unvalidated host AFTER the guard ran — the classic SSRF bypass
        # (169.254.169.254, localhost, internal services). Same stance as the
        # PRM probe below. A discovery document that redirects is rejected
        # rather than silently followed.
        resp = await client.get(url, headers={"Accept": "application/json"}, follow_redirects=False)
    if resp.is_redirect:
        raise HTTPException(
            status_code=400,
            detail=f"Discovery document {url} redirected — refusing to follow (SSRF guard).",
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Discovery document {url} returned {resp.status_code}")
    try:
        data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Discovery document {url} was not JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"Discovery document {url} was not a JSON object")
    return data


async def _oauth_probe_challenge(url: str) -> str | None:
    """Send an unauthenticated MCP initialize and return the WWW-Authenticate header.

    An OAuth-protected server answers with 401 + a Bearer challenge carrying the
    RFC 9728 ``resource_metadata`` pointer. Returns None when the server does not
    challenge (i.e. it is not OAuth-protected on this path).
    """
    safe_url = _validate_mcp_url(url)
    await _assert_discovery_host_allowed(safe_url)
    headers = _build_headers(None, None)
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(safe_url, headers=headers, json={  # codeql[py/full-ssrf]: URL validated by _validate_mcp_url.
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "ai-employee-orchestrator", "version": "1.0.0"}},
            })
        except httpx.RequestError as exc:
            raise HTTPException(status_code=400, detail=f"Could not reach MCP server: {_short_error(str(exc))}") from exc
    return resp.headers.get("www-authenticate")


async def _register_oauth_client(registration_endpoint: str, redirect_uri: str) -> dict:
    """RFC 7591 Dynamic Client Registration against the MCP authorization server."""
    from app.services import mcp_oauth_client as oc
    await _assert_mcp_url_allowed(registration_endpoint)
    body = oc.build_registration_request(redirect_uri=redirect_uri, client_name="AI-Employee")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(registration_endpoint, json=body,
                                 headers={"Accept": "application/json"}, follow_redirects=False)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=400,
                            detail=f"Dynamic client registration failed ({resp.status_code})")
    try:
        data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Registration response was not JSON") from exc
    if not data.get("client_id"):
        raise HTTPException(status_code=400, detail="Registration response had no client_id")
    return data


class OAuthDiscoverRequest(BaseModel):
    # Optional manual client_id for authorization servers that do not offer DCR.
    client_id: str | None = None


@router.post("/{server_id}/oauth/discover")
async def oauth_discover(
    server_id: int, body: OAuthDiscoverRequest | None = None,
    user=Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Discover a server's OAuth configuration (RFC 9728 → RFC 8414) and store it.

    Registers the orchestrator as an OAuth client via DCR when the AS offers it;
    otherwise a ``client_id`` must be supplied (some Entra/Okta setups pre-register
    the client). Does not obtain any token — that happens in the Connect flow.
    """
    from app.services import mcp_oauth_client as oc
    from app.core.encryption import encrypt_token

    server = await db.get(McpServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")

    www_auth = await _oauth_probe_challenge(server.url)
    prm_url = oc.resource_metadata_url(www_auth, server.url)
    if not prm_url:
        raise HTTPException(status_code=400,
                            detail="Server did not advertise OAuth (no WWW-Authenticate resource_metadata)")

    prm = await _oauth_fetch_json(prm_url)
    issuer = oc.pick_authorization_server(prm)
    if not issuer:
        raise HTTPException(status_code=400, detail="Protected-resource metadata lists no authorization server")

    as_meta = None
    for candidate in oc.as_metadata_urls(issuer):
        try:
            as_meta = await _oauth_fetch_json(candidate)
            break
        except HTTPException:
            continue
    if not as_meta:
        raise HTTPException(status_code=400, detail="Could not fetch authorization-server metadata")

    endpoints = oc.select_endpoints(as_meta)
    if not endpoints["authorization_endpoint"] or not endpoints["token_endpoint"]:
        raise HTTPException(status_code=400, detail="Authorization server metadata is missing endpoints")

    server.oauth_enabled = True
    server.oauth_authorization_endpoint = endpoints["authorization_endpoint"]
    server.oauth_token_endpoint = endpoints["token_endpoint"]
    server.oauth_registration_endpoint = endpoints["registration_endpoint"]
    server.oauth_scope = oc.default_scope(prm, as_meta) or server.oauth_scope
    server.oauth_resource = prm.get("resource") or server.oauth_resource

    registered = False
    if body and body.client_id:
        server.oauth_client_id = body.client_id.strip()
        server.oauth_client_secret_encrypted = None
    elif not server.oauth_client_id and endpoints["registration_endpoint"]:
        reg = await _register_oauth_client(endpoints["registration_endpoint"], _callback_redirect_uri())
        server.oauth_client_id = reg["client_id"]
        secret = reg.get("client_secret")
        server.oauth_client_secret_encrypted = encrypt_token(secret) if secret else None
        registered = True

    await db.commit()
    await db.refresh(server)
    return {
        "oauth_enabled": True,
        "authorization_endpoint": server.oauth_authorization_endpoint,
        "token_endpoint": server.oauth_token_endpoint,
        "registration_endpoint": server.oauth_registration_endpoint,
        "scope": server.oauth_scope,
        "resource": server.oauth_resource,
        "client_id": server.oauth_client_id,
        "dynamically_registered": registered,
        "needs_client_id": not server.oauth_client_id,
        "redirect_uri": _callback_redirect_uri(),
    }


@router.get("/{server_id}/oauth/connect")
async def oauth_connect(
    server_id: int, user=Depends(require_admin),
    db: AsyncSession = Depends(get_db), redis: RedisService = Depends(get_redis_service),
):
    """Begin authorization_code + PKCE: return the URL to open in the browser."""
    from app.services import mcp_oauth_client as oc

    server = await db.get(McpServer, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    if not server.oauth_enabled or not server.oauth_authorization_endpoint:
        raise HTTPException(status_code=400, detail="Run OAuth discovery for this server first")
    if not server.oauth_client_id:
        raise HTTPException(status_code=400, detail="No client_id — provide one or use a server that supports DCR")

    verifier, challenge = oc.gen_pkce()
    state = secrets.token_urlsafe(32)
    await redis.client.setex(_STATE_PREFIX + state, _STATE_TTL_SECONDS, json_mod.dumps({
        "server_id": server.id, "code_verifier": verifier, "user_id": str(user.id),
    }))
    auth_url = oc.build_authorization_url(
        authorization_endpoint=server.oauth_authorization_endpoint,
        client_id=server.oauth_client_id,
        redirect_uri=_callback_redirect_uri(),
        state=state,
        code_challenge=challenge,
        scope=server.oauth_scope or "",
        resource=server.oauth_resource or None,
    )
    return {"authorization_url": auth_url}


@router.get("/oauth/callback")
async def oauth_callback(
    request: Request, db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """Authorization-server redirect target: exchange the code for tokens.

    Public route (the browser arrives here after the IdP redirect); the CSRF and
    server binding come from the single-use ``state`` stored in Redis by
    :func:`oauth_connect`, not from an auth dependency.
    """
    from app.services import mcp_oauth_client as oc
    from app.services.mcp_oauth_refresh import (
        OAuthTokenError, apply_token_to_server, perform_token_request,
    )
    from app.core.encryption import decrypt_token

    q = request.query_params
    if q.get("error"):
        return _integrations_redirect("error", detail=q.get("error", "authorization_failed"))

    code = q.get("code", "")
    state = q.get("state", "")
    if not code or not state:
        return _integrations_redirect("error", detail="missing code or state")

    key = _STATE_PREFIX + state
    raw = await redis.client.get(key)
    if raw is None:
        return _integrations_redirect("error", detail="invalid or expired state")
    await redis.client.delete(key)  # single-use
    st = json_mod.loads(raw)

    server = await db.get(McpServer, st["server_id"])
    if not server or not server.oauth_token_endpoint or not server.oauth_client_id:
        return _integrations_redirect("error", detail="server not found or not configured")

    client_secret = (
        decrypt_token(server.oauth_client_secret_encrypted)
        if server.oauth_client_secret_encrypted else None
    )
    data = oc.build_token_exchange_data(
        code=code,
        code_verifier=st["code_verifier"],
        client_id=server.oauth_client_id,
        redirect_uri=_callback_redirect_uri(),
        client_secret=client_secret,
        resource=server.oauth_resource or None,
    )
    try:
        parsed = await perform_token_request(server.oauth_token_endpoint, data)
    except OAuthTokenError as exc:
        _mark_health(server, MCP_HEALTH_AUTH_FAILED, str(exc))
        await db.commit()
        return _integrations_redirect("error", server=server.name, detail=_short_error(str(exc)) or "token exchange failed")

    apply_token_to_server(server, parsed)
    _mark_health(server, MCP_HEALTH_OK)
    await db.commit()
    return _integrations_redirect("connected", server=server.name)
