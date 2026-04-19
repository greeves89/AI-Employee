"""
Computer-Use API — manages bridge sessions between agents and local desktop bridges.

Architecture:
  Agent → MCP Tool → POST /computer-use/command/{session_id}
        → Redis pubsub "cu:{session_id}:cmd"
          → Bridge WebSocket receives command
            → Executes (screenshot, AX tree, click, type...)
              → sends result back via WebSocket
                → Redis pubsub "cu:{session_id}:result"
                  → POST /computer-use/command response returns result
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.dependencies import require_auth, require_auth_or_agent
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/computer-use", tags=["computer-use"])

# In-memory session registry (session_id → {user_id, created_at, bridge_ws, action_count, ...})
_sessions: dict[str, dict] = {}
_redis: RedisService | None = None

SESSION_TIMEOUT_SECS = 30 * 60   # 30 minutes
MAX_ACTIONS_PER_SESSION = 50


def init_computer_use(redis: RedisService) -> None:
    global _redis
    _redis = redis


# ── Session Management ────────────────────────────────────────────────────────

class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    ws_url: str


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(user=Depends(require_auth)):
    """Create a new computer-use bridge session. Returns session_id + WS URL for the bridge."""
    session_id = uuid.uuid4().hex[:12]
    _sessions[session_id] = {
        "user_id": str(user.id),
        "created_at": time.time(),
        "bridge_connected": False,
        "bridge_ws": None,
        "action_count": 0,
        "audit_log": [],
    }
    logger.info(f"Created computer-use session {session_id} for user {user.id}")
    return {
        "session_id": session_id,
        "status": "waiting_for_bridge",
        "ws_url": f"/ws/computer-use/bridge?session_id={session_id}",
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user=Depends(require_auth)):
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "status": "connected" if session["bridge_connected"] else "waiting_for_bridge",
        "created_at": session["created_at"],
    }


@router.get("/sessions")
async def list_sessions(user=Depends(require_auth)):
    user_sessions = [
        {"session_id": sid, "status": "connected" if s["bridge_connected"] else "waiting_for_bridge",
         "created_at": s["created_at"]}
        for sid, s in _sessions.items()
        if s["user_id"] == str(user.id)
    ]
    return {"sessions": user_sessions}


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


# ── Command Relay (called by agent via MCP tool) ──────────────────────────────

class CommandRequest(BaseModel):
    action: str
    params: dict[str, Any] = {}
    timeout: float = 10.0


@router.get("/sessions/{session_id}/audit")
async def get_audit_log(session_id: str, user=Depends(require_auth)):
    """Return the audit log for a session (all actions taken)."""
    session = _sessions.get(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session_id,
        "action_count": session["action_count"],
        "audit_log": session["audit_log"],
    }


@router.post("/sessions/{session_id}/command")
async def send_command(session_id: str, req: CommandRequest, user=Depends(require_auth_or_agent)):
    """Send a command to the bridge and wait for result. Used by agent MCP tool."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Security gate: session timeout
    if time.time() - session["created_at"] > SESSION_TIMEOUT_SECS:
        _sessions.pop(session_id, None)
        raise HTTPException(status_code=410, detail="Session expired (30 min timeout). Create a new session.")

    # Security gate: action limit
    if session["action_count"] >= MAX_ACTIONS_PER_SESSION:
        raise HTTPException(status_code=429, detail=f"Session action limit reached ({MAX_ACTIONS_PER_SESSION} actions max).")

    if not session["bridge_connected"] or not session["bridge_ws"]:
        raise HTTPException(status_code=503, detail="Bridge not connected")

    cmd_id = uuid.uuid4().hex[:8]
    command_msg = json.dumps({
        "type": "command",
        "id": cmd_id,
        "command": {"action": req.action, "params": req.params},
    })

    # Audit log entry
    session["action_count"] += 1
    session["audit_log"].append({
        "cmd_id": cmd_id,
        "action": req.action,
        "params": req.params,
        "ts": time.time(),
    })

    # Register result waiter
    result_future: asyncio.Future = asyncio.get_event_loop().create_future()
    session.setdefault("pending_results", {})[cmd_id] = result_future

    try:
        await session["bridge_ws"].send_text(command_msg)
        result = await asyncio.wait_for(result_future, timeout=req.timeout)
        logger.info(f"[computer-use] session={session_id} action={req.action} #{session['action_count']}")
        return {"result": result}
    except asyncio.TimeoutError:
        session.get("pending_results", {}).pop(cmd_id, None)
        raise HTTPException(status_code=504, detail=f"Bridge command timed out after {req.timeout}s")
    except Exception as e:
        session.get("pending_results", {}).pop(cmd_id, None)
        raise HTTPException(status_code=500, detail=str(e))


# ── Bridge WebSocket ──────────────────────────────────────────────────────────

ws_router = APIRouter(prefix="/ws/computer-use", tags=["computer-use-ws"])


@ws_router.websocket("/bridge")
async def bridge_websocket(websocket: WebSocket, session_id: str | None = None):
    """WebSocket endpoint for the local bridge app."""
    await websocket.accept()

    # Authenticate via query param token
    token = websocket.query_params.get("token", "")
    user_id = await _authenticate_ws(websocket, token)
    if not user_id:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    # Create or join session
    if not session_id or session_id not in _sessions:
        # Auto-create session
        session_id = uuid.uuid4().hex[:12]
        _sessions[session_id] = {
            "user_id": user_id,
            "created_at": time.time(),
            "bridge_connected": False,
            "bridge_ws": None,
            "action_count": 0,
            "audit_log": [],
        }

    session = _sessions.get(session_id)
    if not session or session["user_id"] != user_id:
        await websocket.close(code=1008, reason="Session not found or unauthorized")
        return

    session["bridge_connected"] = True
    session["bridge_ws"] = websocket
    logger.info(f"Bridge connected for session {session_id}")

    # Send session info to bridge
    await websocket.send_text(json.dumps({
        "type": "session_info",
        "session_id": session_id,
    }))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "result":
                cmd_id = msg.get("id", "")
                result = msg.get("result", {})
                # Resolve waiting future
                pending = session.get("pending_results", {})
                future = pending.pop(cmd_id, None)
                if future and not future.done():
                    future.set_result(result)

            elif msg_type == "hello":
                logger.info(f"Bridge hello: {msg.get('capabilities')} on {msg.get('platform')}")
                session["capabilities"] = msg.get("capabilities", [])
                session["platform"] = msg.get("platform", "unknown")

            elif msg_type == "pong":
                pass  # Keepalive

    except WebSocketDisconnect:
        logger.info(f"Bridge disconnected for session {session_id}")
    finally:
        session["bridge_connected"] = False
        session["bridge_ws"] = None
        # Fail all pending commands
        for future in session.get("pending_results", {}).values():
            if not future.done():
                future.set_exception(RuntimeError("Bridge disconnected"))
        session["pending_results"] = {}


async def _authenticate_ws(websocket: WebSocket, token: str) -> str | None:
    """Validate JWT token from query param. Returns user_id or None."""
    if not token:
        # Try Authorization header
        token = websocket.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return None
    try:
        from app.core.auth import decode_token
        payload = decode_token(token)
        return str(payload.get("sub") or payload.get("user_id") or "")
    except Exception:
        return None
