"""MCP HTTP Server per Second Brain.

Exposes each Second Brain vault as a proper MCP server endpoint that n8n,
Cursor and other MCP clients can connect to via the 2025-06-18 Streamable HTTP
transport — the same pattern as the per-agent server (``mcp_agent.py``).

Endpoint: POST /api/v1/mcp/brains/{slug}
Auth:      Authorization: Bearer <brain MCP token>   (generated in the UI)
           or an access token of the corporate IdP (OAuth 2.1, #1195) when the
           admin enabled it — see core/mcp_idp_auth.py.

Supported MCP methods:
  initialize                → server capabilities + brain info
  notifications/initialized → ack (no-op)
  ping                      → pong
  tools/list                → brain_search, brain_read, brain_list
  tools/call                → execute a tool against the vault
"""
import hmac
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Reuse the exact JSON-RPC envelope helpers + protocol version from the agent
# MCP server — one MCP implementation, no parallel copy.
from app.api.mcp_agent import (
    MCP_PROTOCOL_VERSION,
    _mcp_error,
    _mcp_result,
    _tool_result,
)
from app.core import mcp_idp_auth, vault
from app.core.encryption import decrypt_token
from app.db.session import get_db
from app.models.second_brain import SecondBrain
from app.services import vault_indexer, vault_search

router = APIRouter(prefix="/mcp", tags=["mcp-brain"])
logger = logging.getLogger(__name__)

BRAIN_TOOLS = [
    {
        "name": "brain_search",
        "description": (
            "Search this shared knowledge vault (Second Brain) for relevant "
            "Markdown notes. Use BEFORE answering support/how-to/troubleshooting "
            "questions. Returns matching files with snippet lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords to search for (error codes, device names, topics).",
                },
                "limit": {
                    "type": "string",
                    "description": "Max number of files to return (default 10, max 50).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "brain_read",
        "description": "Read the full Markdown content of one vault file by its path (as returned by brain_search or brain_list).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative file path, e.g. 'Drucker/x17137.md'.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "brain_list",
        "description": "List all files in this vault so you can pick one to read.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_tree",
        "description": "Show the folder/file STRUCTURE of this vault as an indented tree — use to understand how the knowledge base is organised before writing a new note.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "brain_write",
        "description": (
            "Create or update a Markdown note in this Second Brain. Use to save "
            "research, articles, procedures or decisions so the knowledge base "
            "grows. Only .md/.markdown/.txt paths; parent folders are created "
            "automatically. Requires a read-write brain."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Vault-relative target path, e.g. 'Wiki/notiz.md'. Must end in .md/.markdown/.txt.",
                },
                "content": {
                    "type": "string",
                    "description": "Full Markdown content of the note (overwrites the file if it already exists).",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Overwrite an existing file. Default true. Set false to fail if the file already exists.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "brain_delete",
        "description": "Delete a note from this Second Brain by its vault-relative path. Requires a read-write brain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Vault-relative file path to delete."},
            },
            "required": ["path"],
        },
    },
]


class _Abgewiesen(Exception):
    """Abweisung mit fertiger Antwort (401 samt WWW-Authenticate, 403, 404)."""

    def __init__(self, antwort: JSONResponse):
        self.antwort = antwort


def _abweisung(status: int, text: str, slug: str, fehler: str = "") -> _Abgewiesen:
    headers = {}
    if status == 401:
        headers["WWW-Authenticate"] = mcp_idp_auth.www_authenticate(f"brains/{slug}", fehler)
    return _Abgewiesen(JSONResponse({"detail": text}, status_code=status, headers=headers))


def _modus(brain: SecondBrain, gewaehrt: str | None) -> str:
    """Wirksamer Modus: Katalog-Obergrenze des Brains, ggf. durch die Freigabe verengt."""
    katalog = (brain.default_mode or "rw").lower()
    if katalog != "rw" or (gewaehrt or "").lower() == "ro":
        return "ro"
    return "rw"


async def brain_zugriff(db: AsyncSession, brain: SecondBrain, user) -> str | None:
    """Darf ``user`` dieses Brain nutzen — und wie? ``None`` = nein.

    Die gemeinsame Rechteprüfung beider Wege: der Brain-Schlüssel ist ein
    Dienstkonto des Brains selbst (``user=None`` → Katalog-Modus), ein IdP-Token
    steht für eine Person und sieht genau die Brains, die sie auch in der
    Oberfläche sieht (``freigegebene_mounts`` + ``fremde_privatablagen``).
    """
    if user is None:
        return _modus(brain, None)
    from app.core.mounts import fremde_privatablagen, freigegebene_mounts

    if brain.label in await fremde_privatablagen(db, [brain.label], user):
        return None
    erlaubt = await freigegebene_mounts(user, db)
    if erlaubt is None:  # Administrator
        return _modus(brain, None)
    if brain.label not in erlaubt:
        return None
    return _modus(brain, erlaubt[brain.label])


async def _auth_brain(slug: str, request: Request, db: AsyncSession) -> tuple[SecondBrain, str]:
    """Brain + wirksamer Modus. Zwei Wege, eine Rechteprüfung:

    * ``Authorization: Bearer <Brain-Schlüssel>`` — Dienstkonten, Clients ohne OAuth;
    * ``Authorization: Bearer <Zugriffstoken des Firmen-IdP>`` (#1195), wenn in den
      Einstellungen eingeschaltet.
    """
    auth_header = request.headers.get("Authorization", "")
    provided = auth_header[7:].strip() if auth_header[:7].lower() == "bearer " else ""
    if not provided:
        raise _abweisung(401, "Invalid or missing Bearer token", slug)

    brain = (
        await db.execute(select(SecondBrain).where(SecondBrain.slug == slug))
    ).scalar_one_or_none()
    if not brain:
        raise _abweisung(404, "Brain not found", slug)
    if not brain.is_active or not brain.mcp_enabled:
        raise _abweisung(403, "MCP access is not enabled for this Second Brain.", slug)

    if brain.mcp_token_encrypted:
        try:
            expected = decrypt_token(brain.mcp_token_encrypted)
        except ValueError:
            raise _abweisung(500, "Token decryption failed (ENCRYPTION_KEY changed?)", slug)
        if hmac.compare_digest(provided, expected):
            return brain, await brain_zugriff(db, brain, None)

    if mcp_idp_auth.aktiv() and mcp_idp_auth.sieht_wie_jwt_aus(provided):
        try:
            user = await mcp_idp_auth.nutzer_aus_bearer(db, provided)
        except mcp_idp_auth.IdpTokenFehler as e:
            logger.info("MCP-Brain %s: IdP-Token abgewiesen (%s)", vault.safe_log(slug), e.grund)
            if e.status == 401:
                raise _abweisung(401, "Invalid or missing Bearer token", slug, "invalid_token")
            raise _abweisung(403, "Kein Zugang für diese Person.", slug)
        modus = await brain_zugriff(db, brain, user)
        if modus is None:
            # 404 statt 403: bestätigt fremde Brains nicht.
            raise _abweisung(404, "Brain not found", slug)
        return brain, modus

    raise _abweisung(401, "Invalid or missing Bearer token", slug, "invalid_token")


@router.post("/brains/{slug}")
async def mcp_brain_endpoint(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """MCP Streamable HTTP endpoint — handles all JSON-RPC requests from MCP clients."""
    try:
        brain, modus = await _auth_brain(slug, request, db)
    except _Abgewiesen as a:
        return a.antwort

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_mcp_error(None, -32700, "Parse error: invalid JSON"), status_code=200)

    if isinstance(body, list):
        responses = []
        for req in body:
            resp = await _handle_rpc(req, brain, db, modus)
            if resp is not None:
                responses.append(resp)
        return JSONResponse(responses)

    resp = await _handle_rpc(body, brain, db, modus)
    if resp is None:
        return JSONResponse(None, status_code=202)  # notification — no body
    return JSONResponse(resp)


async def _handle_rpc(req: dict, brain: SecondBrain, db: AsyncSession, modus: str = "ro"):
    """Dispatch a single JSON-RPC request (or None for notifications)."""
    rpc_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    if rpc_id is None:  # notification (e.g. notifications/initialized)
        return None

    if method == "initialize":
        return _mcp_result(rpc_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": f"ai-employee-brain/{brain.slug}", "version": "1.0.0"},
        })

    if method == "ping":
        return _mcp_result(rpc_id, {})

    if method == "tools/list":
        return _mcp_result(rpc_id, {"tools": BRAIN_TOOLS})

    if method == "tools/call":
        result = await _call_tool(params.get("name", ""), params.get("arguments", {}), brain, db, modus)
        return _mcp_result(rpc_id, result)

    return _mcp_error(rpc_id, -32601, f"Method not found: {method}")


async def _call_tool(name: str, args: dict, brain: SecondBrain, db: AsyncSession, modus: str = "ro") -> dict:
    """Execute a vault tool and return an MCP tool result."""
    if name == "brain_search":
        query = (args.get("query") or "").strip()
        if not query:
            return _tool_result("Error: 'query' is required", is_error=True)
        try:
            limit = min(int(float(str(args.get("limit", 10)))), 50)
        except (ValueError, TypeError):
            limit = 10
        hits = await vault_search.hybrid_search(db, brain.label, brain.host_path, query, limit)
        if not hits:
            return _tool_result(f"No matches for '{query}' in {brain.name}.")
        lines = [f"{len(hits)} match(es) in {brain.name}:"]
        for h in hits:
            lines.append(f"\n## {h['path']}")
            for s in h["snippets"]:
                lines.append(f"  · {s}")
        return _tool_result("\n".join(lines))

    if name == "brain_read":
        path = (args.get("path") or "").strip()
        if not path:
            return _tool_result("Error: 'path' is required", is_error=True)
        try:
            content = vault.read_file(brain.host_path, path)
        except FileNotFoundError:
            return _tool_result(f"File not found: {path}", is_error=True)
        except ValueError as e:
            return _tool_result(f"Error: {e}", is_error=True)
        return _tool_result(content)

    if name == "brain_list":
        entries = vault.list_entries(brain.host_path)
        files = [e["path"] for e in entries if e["type"] == "file"]
        if not files:
            return _tool_result(f"{brain.name} is empty.")
        return _tool_result(
            f"{len(files)} file(s) in {brain.name}:\n" + "\n".join(f"  {p}" for p in files)
        )

    if name == "brain_tree":
        return _tool_result(f"Structure of {brain.name}:\n{vault.tree_text(brain.host_path)}")

    if name == "brain_write":
        if modus != "rw":
            return _tool_result(f"'{brain.name}' is read-only — writing is not permitted.", is_error=True)
        path = (args.get("path") or "").strip()
        content = args.get("content")
        if not path or content is None:
            return _tool_result("Error: 'path' and 'content' are required", is_error=True)
        overwrite = args.get("overwrite", True) is not False
        try:
            res = vault.write_file(brain.host_path, path, str(content), overwrite=overwrite)
        except FileExistsError:
            return _tool_result(f"File already exists: {path} (set overwrite=true to replace).", is_error=True)
        except ValueError as e:
            return _tool_result(f"Error: {e}", is_error=True)
        logger.info("brain_write brain=%s path=%s created=%s bytes=%s", vault.safe_log(brain.slug), vault.safe_log(path), res["created"], res["bytes"])
        try:
            await vault_indexer.index_file(db, brain.label, brain.host_path, path)
        except Exception as e:  # indexing must not fail the write
            logger.warning("brain_write reindex failed brain=%s path=%s: %s", brain.slug, vault.safe_log(path), vault.safe_log(e))
        verb = "Created" if res["created"] else "Updated"
        return _tool_result(f"{verb} {path} in {brain.name} ({res['bytes']} bytes).")

    if name == "brain_delete":
        if modus != "rw":
            return _tool_result(f"'{brain.name}' is read-only — deleting is not permitted.", is_error=True)
        path = (args.get("path") or "").strip()
        if not path:
            return _tool_result("Error: 'path' is required", is_error=True)
        try:
            vault.delete_file(brain.host_path, path)
        except FileNotFoundError:
            return _tool_result(f"File not found: {path}", is_error=True)
        except ValueError as e:
            return _tool_result(f"Error: {e}", is_error=True)
        logger.info("brain_delete brain=%s path=%s", vault.safe_log(brain.slug), vault.safe_log(path))
        try:
            await vault_indexer.remove_file(db, brain.label, path)
        except Exception as e:
            logger.warning("brain_delete deindex failed brain=%s path=%s: %s", brain.slug, vault.safe_log(path), vault.safe_log(e))
        return _tool_result(f"Deleted {path} from {brain.name}.")

    return _tool_result(f"Unknown tool: {name}", is_error=True)
