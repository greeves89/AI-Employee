"""
Computer-Use API — manages bridge sessions between agents and local desktop bridges.

Architecture:
  User opens bridge app on their PC
    → Bridge authenticates with user JWT, must provide an existing session_id
      → Only the session owner's agents can send commands to that session

Security model:
  - Sessions are created by users (require_auth)
  - Bridge WS must present valid JWT + matching session_id (no auto-create)
  - Agents (HMAC token) are verified against agent.user_id == session.user_id
  - Agents can list sessions for their own user via require_auth_or_agent
  - Capability groups restrict which actions agents may invoke (enforced server-side)
"""
import asyncio
import json
import logging
import time
import uuid
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_auth, require_auth_or_agent
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/computer-use", tags=["computer-use"])

# In-memory session registry (session_id → {user_id, created_at, bridge_ws, ...})
_sessions: dict[str, dict] = {}
_redis: RedisService | None = None

SESSION_TIMEOUT_SECS = 30 * 60
MAX_ACTIONS_PER_SESSION = 50


# ── Capability groups ─────────────────────────────────────────────────────────

# Map capability-group name → list of allowed action strings
CAPABILITY_GROUPS: dict[str, list[str]] = {
    "screenshots": ["screenshot", "get_mouse_position"],
    "mouse": ["mouse_move", "mouse_click", "mouse_scroll", "drag"],
    "keyboard": ["key", "type", "hotkey"],
    "accessibility": ["ax_tree"],
    "apps": ["open_app", "close_app"],
    "clipboard": ["clipboard_read", "clipboard_write"],
    "shell": ["shell_run"],
}

# Groups enabled for all new sessions unless the user changes them.
# shell and clipboard are off by default for safety.
DEFAULT_ALLOWED_CAPABILITIES: set[str] = {
    "screenshots",
    "mouse",
    "keyboard",
    "accessibility",
    "apps",
}

# Reverse map: action → capability group
_ACTION_TO_GROUP: dict[str, str] = {
    action: group
    for group, actions in CAPABILITY_GROUPS.items()
    for action in actions
}


def _action_allowed(action: str, allowed: set[str]) -> bool:
    """Return True if the action is covered by at least one allowed capability group."""
    group = _ACTION_TO_GROUP.get(action)
    if group is None:
        # Unknown actions (e.g. future bridge commands) — block by default
        return False
    return group in allowed


def init_computer_use(redis: RedisService) -> None:
    global _redis
    _redis = redis


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _resolve_caller_user_id(caller, db: AsyncSession) -> str | None:
    """Return the user_id for a caller (User object or agent SimpleNamespace)."""
    if hasattr(caller, "role") and caller.role == "agent":
        from sqlalchemy import select
        from app.models.agent import Agent
        agent = await db.scalar(select(Agent).where(Agent.id == caller.id))
        if not agent or not agent.user_id:
            return None
        return str(agent.user_id)
    return str(caller.id)


def _session_view(sid: str, s: dict) -> dict:
    return {
        "session_id": sid,
        "status": "connected" if s["bridge_connected"] else "waiting_for_bridge",
        "created_at": s["created_at"],
        "action_count": s["action_count"],
        "platform": s.get("platform", "unknown"),
        "capabilities": s.get("capabilities", []),
        "allowed_capabilities": sorted(s.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)),
        "last_disconnected_at": s.get("last_disconnected_at"),
        "bridge_last_seen_at": s.get("bridge_last_seen_at"),
        "agent_id": s.get("agent_id"),
    }


# ── Session Management ────────────────────────────────────────────────────────

class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    ws_url: str
    allowed_capabilities: list[str]


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(user=Depends(require_auth)):
    """Create a new bridge session. Returns session_id + WS URL for the bridge app."""
    session_id = uuid.uuid4().hex[:12]
    allowed = set(DEFAULT_ALLOWED_CAPABILITIES)
    _sessions[session_id] = {
        "user_id": str(user.id),
        "created_at": time.time(),
        "bridge_connected": False,
        "bridge_ws": None,
        "action_count": 0,
        "audit_log": [],
        "pending_results": {},
        "allowed_capabilities": allowed,
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "agent_id": None,
    }
    logger.info(f"Created computer-use session {session_id} for user {user.id}")
    return {
        "session_id": session_id,
        "status": "waiting_for_bridge",
        "ws_url": f"/ws/computer-use/bridge?session_id={session_id}",
        "allowed_capabilities": sorted(allowed),
    }


@router.get("/sessions")
async def list_sessions(
    caller=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """List sessions for the calling user (works for both user JWT and agent HMAC token)."""
    user_id = await _resolve_caller_user_id(caller, db)
    if not user_id:
        raise HTTPException(status_code=403, detail="Cannot resolve user for this agent")

    # Purge expired sessions for this user on read
    expired = [sid for sid, s in _sessions.items() if time.time() - s["created_at"] > SESSION_TIMEOUT_SECS]
    for sid in expired:
        _sessions.pop(sid, None)

    user_sessions = [
        _session_view(sid, s)
        for sid, s in _sessions.items()
        if s["user_id"] == user_id
    ]
    return {"sessions": user_sessions}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user=Depends(require_auth)):
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_view(session_id, session)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(require_auth)):
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    ws = session.get("bridge_ws")
    if ws:
        try:
            await ws.close()
        except Exception:
            pass
    _sessions.pop(session_id, None)
    return {"ok": True}


class CapabilityUpdate(BaseModel):
    allowed_capabilities: list[str]


@router.patch("/sessions/{session_id}/capabilities")
async def update_capabilities(
    session_id: str,
    req: CapabilityUpdate,
    user=Depends(require_auth),
):
    """Update which capability groups are allowed for this session."""
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")

    unknown = set(req.allowed_capabilities) - set(CAPABILITY_GROUPS.keys())
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown capability groups: {sorted(unknown)}")

    session["allowed_capabilities"] = set(req.allowed_capabilities)
    logger.info(f"Session {session_id}: capabilities updated to {sorted(req.allowed_capabilities)}")
    return {
        "session_id": session_id,
        "allowed_capabilities": sorted(session["allowed_capabilities"]),
    }


class AgentAssignment(BaseModel):
    agent_id: str | None = None


@router.patch("/sessions/{session_id}/agent")
async def assign_agent(
    session_id: str,
    req: AgentAssignment,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Assign (or unassign) an agent to this session. Only that agent may then send commands."""
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")

    if req.agent_id is not None:
        from sqlalchemy import select
        from app.models.agent import Agent
        agent = await db.scalar(select(Agent).where(Agent.id == req.agent_id))
        if not agent or str(agent.user_id) != str(user.id):
            raise HTTPException(status_code=404, detail="Agent not found or not yours")

    session["agent_id"] = req.agent_id
    logger.info(f"Session {session_id}: agent_id set to {req.agent_id}")
    return _session_view(session_id, session)


@router.get("/capabilities")
async def list_capability_groups(_=Depends(require_auth)):
    """Return all known capability groups and their included actions."""
    return {
        "groups": [
            {
                "id": group_id,
                "actions": actions,
                "default": group_id in DEFAULT_ALLOWED_CAPABILITIES,
            }
            for group_id, actions in CAPABILITY_GROUPS.items()
        ]
    }


@router.get("/sessions/{session_id}/audit")
async def get_audit_log(session_id: str, user=Depends(require_auth)):
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "action_count": session["action_count"],
        "audit_log": session["audit_log"],
    }


# ── Command Relay (called by agent via MCP tool) ──────────────────────────────

class CommandRequest(BaseModel):
    action: str
    params: dict[str, Any] = {}
    timeout: float = 10.0


@router.post("/sessions/{session_id}/command")
async def send_command(
    session_id: str,
    req: CommandRequest,
    caller=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Relay a command to the bridge. Verifies caller owns (or belongs to the owner of) this session."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Ownership check: resolve caller's user_id and compare to session owner
    caller_user_id = await _resolve_caller_user_id(caller, db)
    if not caller_user_id:
        raise HTTPException(status_code=403, detail="Cannot verify ownership — agent has no user_id")
    if caller_user_id != session["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied: this session belongs to a different user")

    # Agent-level restriction: if session is assigned to a specific agent, enforce it
    assigned_agent_id = session.get("agent_id")
    if assigned_agent_id and hasattr(caller, "role") and caller.role == "agent":
        if str(caller.id) != str(assigned_agent_id):
            raise HTTPException(status_code=403, detail="This session is assigned to a different agent")

    # Session timeout
    if time.time() - session["created_at"] > SESSION_TIMEOUT_SECS:
        _sessions.pop(session_id, None)
        raise HTTPException(status_code=410, detail="Session expired (30 min). Create a new session.")

    # Action limit
    if session["action_count"] >= MAX_ACTIONS_PER_SESSION:
        raise HTTPException(
            status_code=429,
            detail=f"Action limit reached ({MAX_ACTIONS_PER_SESSION}/session).",
        )

    # Capability check — enforce server-side
    allowed: set[str] = session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)
    if not _action_allowed(req.action, allowed):
        group = _ACTION_TO_GROUP.get(req.action, "unknown")
        raise HTTPException(
            status_code=403,
            detail=f"Action '{req.action}' is not permitted (capability group '{group}' is disabled for this session).",
        )

    if not session["bridge_connected"] or not session["bridge_ws"]:
        raise HTTPException(status_code=503, detail="Bridge not connected")

    cmd_id = uuid.uuid4().hex[:8]
    command_msg = json.dumps({
        "type": "command",
        "id": cmd_id,
        "command": {"action": req.action, "params": req.params},
    })

    session["action_count"] += 1
    session["audit_log"].append({
        "cmd_id": cmd_id,
        "action": req.action,
        "params": req.params,
        "caller": str(getattr(caller, "id", "?")),
        "ts": time.time(),
    })

    result_future: asyncio.Future = asyncio.get_event_loop().create_future()
    session["pending_results"][cmd_id] = result_future

    try:
        await session["bridge_ws"].send_text(command_msg)
        result = await asyncio.wait_for(result_future, timeout=req.timeout)
        logger.info(f"[computer-use] session={session_id} action={req.action} #{session['action_count']}")
        return {"result": result}
    except asyncio.TimeoutError:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=504, detail=f"Bridge timed out after {req.timeout}s")
    except Exception as e:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=500, detail=str(e))


# ── Session status (lightweight — lets UI distinguish "no screenshot yet" from "bridge gone") ──

@router.get("/sessions/{session_id}/status")
async def get_session_status(
    session_id: str,
    user=Depends(require_auth),
):
    """Return bridge connection state without triggering a screenshot.

    Stale check: if bridge_last_seen_at is >20s ago the bridge is considered
    gone even if bridge_connected is True (NAT/WiFi drop, no TCP FIN).
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(user.id) != session["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    now = time.time()
    last_seen = session.get("bridge_last_seen_at")
    stale = last_seen is None or (now - last_seen) > 20
    connected = session["bridge_connected"] and not stale

    return {
        "bridge_connected": connected,
        "bridge_last_seen_at": last_seen,
        "last_disconnected_at": session.get("last_disconnected_at"),
        "allowed_capabilities": sorted(session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)),
        "platform": session.get("platform"),
        "action_count": session["action_count"],
    }


# ── Screenshot endpoint (frontend live view) ──────────────────────────────────

@router.get("/sessions/{session_id}/screenshot")
async def get_screenshot(
    session_id: str,
    user=Depends(require_auth),
):
    """Request a screenshot from the bridge and return base64 PNG. Caches 3s."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(user.id) != session["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Return cached screenshot if still fresh
    cached = session.get("last_screenshot")
    if cached and time.time() - cached["ts"] < 3:
        return {"screenshot_b64": cached["data"], "ts": cached["ts"]}

    if not session["bridge_connected"] or not session["bridge_ws"]:
        raise HTTPException(
            status_code=503,
            detail="bridge_disconnected",
            headers={"X-Bridge-Status": "disconnected"},
        )

    cmd_id = uuid.uuid4().hex[:8]
    command_msg = json.dumps({
        "type": "command",
        "id": cmd_id,
        "command": {"action": "screenshot", "params": {"scale": 0.5}},
    })
    result_future: asyncio.Future = asyncio.get_event_loop().create_future()
    session["pending_results"][cmd_id] = result_future

    try:
        await session["bridge_ws"].send_text(command_msg)
        result = await asyncio.wait_for(result_future, timeout=15.0)
        screenshot_b64 = result.get("screenshot_b64", "")
        ts = time.time()
        session["last_screenshot"] = {"data": screenshot_b64, "ts": ts}
        return {"screenshot_b64": screenshot_b64, "ts": ts}
    except asyncio.TimeoutError:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=504, detail="Bridge timed out")
    except Exception as e:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=500, detail=str(e))


# ── Bridge WebSocket ──────────────────────────────────────────────────────────

ws_router = APIRouter(prefix="/ws/computer-use", tags=["computer-use-ws"])


@ws_router.websocket("/bridge")
async def bridge_websocket(websocket: WebSocket, session_id: str | None = None):
    """
    WebSocket endpoint for the local bridge app.

    Rules:
    - session_id MUST be provided and MUST already exist (no auto-create)
    - JWT token must belong to the session owner
    """
    await websocket.accept()

    # Authenticate
    token = websocket.query_params.get("token", "")
    user_id = await _authenticate_ws(websocket, token)
    if not user_id:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    # Require explicit session_id — no auto-create
    if not session_id:
        await websocket.close(code=1008, reason="session_id required: create a session first via POST /computer-use/sessions")
        return

    session = _sessions.get(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found — it may have expired. Create a new one.")
        return

    # Ownership: token user must match session owner
    if session["user_id"] != user_id:
        await websocket.close(code=1008, reason="Unauthorized: session belongs to a different user")
        return

    # Only one bridge per session
    if session["bridge_connected"]:
        await websocket.close(code=1008, reason="Session already has an active bridge connection")
        return

    session["bridge_connected"] = True
    session["bridge_ws"] = websocket
    session["bridge_last_seen_at"] = time.time()
    logger.info(f"Bridge connected for session {session_id} (user {user_id})")

    await websocket.send_text(json.dumps({
        "type": "session_info",
        "session_id": session_id,
        "allowed_capabilities": sorted(session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)),
    }))

    async def _ping_loop():
        """Send ping every 10s. NAT/WiFi drops don't send TCP FIN, so without
        a heartbeat bridge_connected stays True forever after a network drop."""
        try:
            while True:
                await asyncio.sleep(10)
                await websocket.send_text(json.dumps({"type": "ping"}))
        except Exception:
            pass

    ping_task = asyncio.create_task(_ping_loop())

    try:
        while True:
            raw = await websocket.receive_text()
            # Update heartbeat timestamp on every incoming message
            session["bridge_last_seen_at"] = time.time()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "result":
                cmd_id = msg.get("id", "")
                result = msg.get("result", {})
                future = session["pending_results"].pop(cmd_id, None)
                if future and not future.done():
                    future.set_result(result)

            elif msg_type == "hello":
                logger.info(f"Bridge hello: caps={msg.get('capabilities')} platform={msg.get('platform')}")
                session["capabilities"] = msg.get("capabilities", [])
                session["platform"] = msg.get("platform", "unknown")

            elif msg_type == "pong":
                pass  # bridge_last_seen_at already updated above

    except WebSocketDisconnect:
        logger.info(f"Bridge disconnected for session {session_id}")
    finally:
        ping_task.cancel()
        session["bridge_connected"] = False
        session["bridge_ws"] = None
        session["last_disconnected_at"] = time.time()
        for future in session["pending_results"].values():
            if not future.done():
                future.set_exception(RuntimeError("Bridge disconnected"))
        session["pending_results"] = {}


async def _authenticate_ws(websocket: WebSocket, token: str) -> str | None:
    """Validate JWT token from query param or Authorization header. Returns user_id or None."""
    if not token:
        token = websocket.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return None
    try:
        from app.core.auth import decode_token
        payload = decode_token(token)
        uid = str(payload.get("sub") or payload.get("user_id") or "")
        return uid or None
    except Exception:
        return None
