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

from app.config import settings
from app.core.log_redaction import scrub_log
from app.dependencies import get_db, is_agent_principal, require_auth, require_auth_or_agent
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/computer-use", tags=["computer-use"])

# In-memory session registry (session_id → {user_id, created_at, bridge_ws, ...})
_sessions: dict[str, dict] = {}
_redis: RedisService | None = None

# Idle timeout, measured from the LAST ACTIVITY (not from creation). The old
# create-based cap killed sessions that were actively in use after 30 minutes.
SESSION_TIMEOUT_SECS = 12 * 3600
# Safety cap against a runaway agent. Counted per session, but the counter is
# reset whenever a bridge (re)attaches, so a long-lived session isn't slowly
# starved into a 429 the way the old hard 50 did.
MAX_ACTIONS_PER_SESSION = 500

# Actions that visibly change the screen — after one of these completes, the
# cached screenshot the human's Live-View tab polls is stale until the next
# 4s tick. Refreshing it right away (event-driven) instead of waiting for a
# blind poll interval is what actually makes the live view feel live.
_SCREEN_CHANGING_ACTIONS = {
    "click", "type", "key", "scroll", "move", "drag", "open_app", "close_app",
}


async def _refresh_screenshot_cache(session_id: str) -> str | None:
    """Pull a fresh screenshot from the bridge right after an action and
    update the session cache, so the next poll from the Live-View tab already
    sees the post-action state instead of a stale one. Returns the base64 PNG
    (or None on failure) so callers that need the image itself — e.g. a
    Replay-Modus recording step — can await this instead of firing it blind."""
    session = _sessions.get(session_id)
    if not session or not session["bridge_connected"] or not session["bridge_ws"]:
        return None
    cmd_id = uuid.uuid4().hex[:8]
    result_future: asyncio.Future = asyncio.get_running_loop().create_future()
    session["pending_results"][cmd_id] = result_future
    try:
        await session["bridge_ws"].send_text(json.dumps({
            "type": "command",
            "id": cmd_id,
            "command": {"action": "screenshot", "params": {"scale": 0.5}},
        }))
        result = await asyncio.wait_for(result_future, timeout=10.0)
        screenshot_b64 = result.get("screenshot_b64", "")
        if screenshot_b64:
            session["last_screenshot"] = {"data": screenshot_b64, "ts": time.time()}
            return screenshot_b64
        return None
    except Exception:
        # Best-effort only — the next explicit poll will fall back to
        # requesting its own fresh screenshot if this cache update failed.
        session["pending_results"].pop(cmd_id, None)
        return None


# ── Capability groups ─────────────────────────────────────────────────────────

# Map capability-group name → list of allowed action strings
CAPABILITY_GROUPS: dict[str, list[str]] = {
    "screenshots": ["screenshot", "get_mouse_position"],
    "mouse": ["mouse_move", "mouse_click", "mouse_scroll", "drag"],
    "keyboard": ["key", "type", "hotkey"],
    "accessibility": ["ax_tree"],
    "apps": ["open_app", "close_app", "open_url"],
    "clipboard": ["clipboard_read", "clipboard_write"],
    "shell": ["shell_run"],
    # Replay-Modus: observe the human's own clicks/keystrokes. Off by default
    # like shell — while active it sees everything typed on that machine.
    "input_capture": ["start_input_capture", "stop_input_capture"],
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


# ── Session persistence ───────────────────────────────────────────────────────
#
# Sessions used to live ONLY in this process dict, so every orchestrator restart
# or deploy invalidated them: the bridge had to be given a brand-new session id
# by hand each time. For Computer-Use to be a normal everyday feature the
# session has to outlive the process.
#
# The live WebSocket object cannot be serialized, so only the METADATA goes to
# Redis; the socket stays in memory and is re-attached when the bridge
# reconnects with the same id (it retries by itself every 5s).

_SESSION_KEY = "computer_use:session:"
# Idle TTL. Refreshed on every write, so an actively used session never expires
# — unlike the old hard 30min-from-creation cap, which killed live sessions
# mid-work.
_SESSION_TTL_SECS = 7 * 24 * 3600

# Fields that make sense to restore; everything else is per-process runtime.
_PERSISTED_FIELDS = (
    "user_id", "created_at", "agent_id", "allowed_capabilities",
    "action_count", "last_activity_at", "platform", "bridge_version",
)


async def _persist_session(session_id: str) -> None:
    """Write session metadata to Redis (best-effort — never breaks a request)."""
    session = _sessions.get(session_id)   # in-memory only: never re-enter _get_session
    if not session or _redis is None or _redis.client is None:
        return
    try:
        payload = {k: session.get(k) for k in _PERSISTED_FIELDS}
        payload["allowed_capabilities"] = sorted(session.get("allowed_capabilities") or [])
        await _redis.client.set(
            _SESSION_KEY + session_id, json.dumps(payload), ex=_SESSION_TTL_SECS
        )
    except Exception:  # noqa: BLE001 — persistence is an optimization, not a gate
        logger.debug("Could not persist computer-use session %s", scrub_log(session_id), exc_info=True)


async def _forget_session(session_id: str) -> None:
    """Session ENDGÜLTIG entfernen — aus dem Speicher UND aus Redis.

    Nur `_sessions.pop()` reicht seit der Redis-Persistenz (v1.130.0) nicht mehr:
    der Schlüssel überlebt, `_restore_session` holt die Session beim nächsten
    Zugriff zurück, und die Liste scannt sie ohnehin wieder ein. Für den Nutzer
    sah das so aus, als liesse sich eine Session nicht löschen — sie war nach
    dem Neuladen einfach wieder da.
    """
    _sessions.pop(session_id, None)
    if _redis is None or _redis.client is None:
        return
    try:
        await _redis.client.delete(_SESSION_KEY + session_id)
    except Exception:  # noqa: BLE001 — Löschen darf keinen Request scheitern lassen
        logger.warning("Could not delete computer-use session %s from Redis", scrub_log(session_id), exc_info=True)


async def _restore_session(session_id: str) -> dict | None:
    """Rehydrate a session from Redis into this process. Returns it, or None."""
    if _redis is None or _redis.client is None:
        return None
    try:
        raw = await _redis.client.get(_SESSION_KEY + session_id)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return None
    session = {
        "user_id": stored.get("user_id"),
        "created_at": stored.get("created_at") or time.time(),
        "last_activity_at": stored.get("last_activity_at") or time.time(),
        "bridge_connected": False,
        "bridge_ws": None,
        "action_count": int(stored.get("action_count") or 0),
        "audit_log": [],          # audit is per-process, not restored
        "pending_results": {},
        "allowed_capabilities": set(stored.get("allowed_capabilities")
                                    or DEFAULT_ALLOWED_CAPABILITIES),
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "agent_id": stored.get("agent_id"),
        "platform": stored.get("platform"),
        "bridge_version": stored.get("bridge_version"),
        "recording": False,
        "recording_steps": [],
        "capture_human": False,
    }
    _sessions[session_id] = session
    logger.info("Restored computer-use session %s from Redis", scrub_log(session_id))
    return session


async def _get_session(session_id: str) -> dict | None:
    """Look up a session, transparently restoring it after a restart."""
    session = _sessions.get(session_id)
    if session is not None:
        return session
    return await _restore_session(session_id)


async def _touch_session(session_id: str) -> None:
    """Mark activity and refresh the TTL — keeps a used session alive."""
    session = _sessions.get(session_id)   # in-memory only: no restore needed here
    if session is None:
        return
    session["last_activity_at"] = time.time()
    await _persist_session(session_id)


async def _find_user_session(user_id: str) -> tuple[str, dict] | None:
    """The user's newest usable session, restoring from Redis if needed.

    Prefers one with a live bridge, so reconnecting the tab never steals the
    session the bridge is currently attached to.
    """
    candidates = [(sid, s) for sid, s in _sessions.items() if s.get("user_id") == user_id]
    if not candidates and _redis is not None and _redis.client is not None:
        try:
            async for key in _redis.client.scan_iter(match=_SESSION_KEY + "*", count=100):
                sid = (key.decode() if isinstance(key, bytes) else key).split(":")[-1]
                restored = await _restore_session(sid)
                if restored and restored.get("user_id") == user_id:
                    candidates.append((sid, restored))
        except Exception:  # noqa: BLE001 — fall through to "create a new one"
            logger.debug("Session scan failed", exc_info=True)
    if not candidates:
        return None
    candidates.sort(
        key=lambda kv: (bool(kv[1].get("bridge_connected")),
                        float(kv[1].get("last_activity_at") or kv[1].get("created_at") or 0)),
        reverse=True,
    )
    return candidates[0]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _public_bridge_base_url() -> str | None:
    configured = settings.bridge_public_url.strip().rstrip("/")
    return configured or None


async def _resolve_caller_user_id(caller, db: AsyncSession) -> str | None:
    """Return the user_id for a caller (User object or agent SimpleNamespace)."""
    if is_agent_principal(caller):
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
        "bridge_version": s.get("bridge_version"),
        "bridge_host": s.get("bridge_host"),
        "bridge_public_url": _public_bridge_base_url(),
        "agent_id": s.get("agent_id"),
        "recording": bool(s.get("recording")),
    }


# ── Session Management ────────────────────────────────────────────────────────

class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    ws_url: str
    allowed_capabilities: list[str]


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(user=Depends(require_auth), reuse: bool = True):
    """Create a bridge session — or hand back the user's existing one.

    ``reuse`` (default on) returns the caller's most recent live session instead
    of minting a new id. Without it every visit to the Computer-Use tab produced
    a fresh id and the bridge had to be reconfigured by hand each time; with it
    the bridge keeps working across restarts and page reloads.
    """
    if reuse:
        existing = await _find_user_session(str(user.id))
        if existing:
            sid, sess = existing
            return {
                "session_id": sid,
                "status": "connected" if sess.get("bridge_connected") else "waiting_for_bridge",
                "ws_url": f"/ws/computer-use/bridge?session_id={sid}",
                "allowed_capabilities": sorted(sess.get("allowed_capabilities") or []),
            }

    session_id = uuid.uuid4().hex[:12]
    allowed = set(DEFAULT_ALLOWED_CAPABILITIES)
    _sessions[session_id] = {
        "user_id": str(user.id),
        "created_at": time.time(),
        "last_activity_at": time.time(),
        "bridge_connected": False,
        "bridge_ws": None,
        "action_count": 0,
        "audit_log": [],
        "pending_results": {},
        "allowed_capabilities": allowed,
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "bridge_host": None,
        "agent_id": None,
        # Replay-Modus: while recording, every screen-changing action is
        # captured as a step (action + params + a screenshot taken right
        # after it) — see /recording/start|stop below.
        "recording": False,
        "recording_steps": [],
        "capture_human": False,
    }
    await _persist_session(session_id)
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
    expired = [
        sid for sid, s in _sessions.items()
        if not s.get("bridge_connected")
        and time.time() - float(s.get("last_activity_at") or s.get("created_at") or 0) > SESSION_TIMEOUT_SECS
    ]
    for sid in expired:
        await _forget_session(sid)

    user_sessions = [
        _session_view(sid, s)
        for sid, s in _sessions.items()
        if s["user_id"] == user_id
    ]
    return {"sessions": user_sessions}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user=Depends(require_auth)):
    session = await _get_session(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_view(session_id, session)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(require_auth)):
    session = await _get_session(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")
    ws = session.get("bridge_ws")
    if ws:
        try:
            await ws.close()
        except Exception:
            pass
    await _forget_session(session_id)
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
    session = await _get_session(session_id)
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
    session = await _get_session(session_id)
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
    session = await _get_session(session_id)
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


async def dispatch_bridge_command(
    session_id: str,
    action: str,
    params: dict[str, Any],
    *,
    caller_user_id: str,
    caller_label: str,
    caller_agent_id: str | None = None,
    timeout: float = 10.0,
) -> Any:
    """Ein Kommando an die Bridge schicken — DER EINE WEG dorthin.

    Sowohl der HTTP-Endpunkt als auch die Realtime-Sprachsitzung gehen hier durch,
    damit die Schutzmechanismen nicht an zwei Stellen gepflegt werden müssen und
    keine Aufruferart einen kürzeren Weg bekommt als die andere: Besitz, Zuordnung
    zu einem bestimmten Agenten, Sitzungs-Timeout, Aktions-Limit, serverseitige
    Capability-Prüfung und der Audit-Eintrag gelten für alle gleich.

    ``caller_user_id`` ist bereits aufgelöst — wer hier hereinkommt, hat seine
    Identität schon nachgewiesen. Fehler kommen als HTTPException, damit der
    Endpunkt sie unverändert weiterreichen kann und der Sprach-Agent eine
    verständliche Ansage daraus machen kann.
    """
    session = await _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not caller_user_id:
        raise HTTPException(status_code=403, detail="Cannot verify ownership — no user_id")
    if caller_user_id != session["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied: this session belongs to a different user")

    # Ist die Sitzung einem bestimmten Agenten zugeordnet, darf nur der sie bedienen.
    assigned_agent_id = session.get("agent_id")
    if assigned_agent_id and caller_agent_id and str(caller_agent_id) != str(assigned_agent_id):
        raise HTTPException(status_code=403, detail="This session is assigned to a different agent")

    req = SimpleNamespace(action=action, params=params or {}, timeout=timeout)
    caller = SimpleNamespace(id=caller_label)

    # Session timeout
    if time.time() - float(session.get("last_activity_at") or session["created_at"]) > SESSION_TIMEOUT_SECS:
        await _forget_session(session_id)
        raise HTTPException(status_code=410, detail="Session abgelaufen. Bitte eine neue Session anlegen.")

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

    result_future: asyncio.Future = asyncio.get_running_loop().create_future()
    session["pending_results"][cmd_id] = result_future

    try:
        await session["bridge_ws"].send_text(command_msg)
        result = await asyncio.wait_for(result_future, timeout=req.timeout)
        logger.info(f"[computer-use] session={scrub_log(session_id)} action={scrub_log(req.action)} #{session['action_count']}")
        await _touch_session(session_id)
        if req.action in _SCREEN_CHANGING_ACTIONS:
            if session.get("recording"):
                # Recording: wait for the screenshot so the step carries the
                # correct post-action image, then append it to the transcript.
                screenshot_b64 = await _refresh_screenshot_cache(session_id)
                session["recording_steps"].append({
                    "action": req.action,
                    "params": req.params,
                    "ts": time.time(),
                    "screenshot_b64": screenshot_b64,
                })
            else:
                asyncio.create_task(_refresh_screenshot_cache(session_id))
        return {"result": result}
    except asyncio.TimeoutError:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=504, detail=f"Bridge timed out after {req.timeout}s")
    except Exception as e:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sessions/{session_id}/command")
async def send_command(
    session_id: str,
    req: CommandRequest,
    caller=Depends(require_auth_or_agent),
    db: AsyncSession = Depends(get_db),
):
    """Relay a command to the bridge. Verifies caller owns (or belongs to the owner of) this session."""
    caller_user_id = await _resolve_caller_user_id(caller, db)
    return await dispatch_bridge_command(
        session_id, req.action, req.params,
        caller_user_id=caller_user_id or "",
        caller_label=str(getattr(caller, "id", "?")),
        caller_agent_id=str(caller.id) if is_agent_principal(caller) else None,
        timeout=req.timeout,
    )


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
    session = await _get_session(session_id)
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
        "bridge_version": session.get("bridge_version"),
        "bridge_host": session.get("bridge_host"),
        "bridge_public_url": _public_bridge_base_url(),
        "action_count": session["action_count"],
    }


# ── Screenshot endpoint (frontend live view) ──────────────────────────────────

@router.get("/sessions/{session_id}/screenshot")
async def get_screenshot(
    session_id: str,
    user=Depends(require_auth),
):
    """Request a screenshot from the bridge and return base64 PNG.

    Caches 1s — short enough that the Live-View tab's 1s poll almost always
    just reads this cache instead of round-tripping to the bridge, while
    _refresh_screenshot_cache() keeps it fresh event-driven after every
    action so the human sees the post-action state immediately either way.
    """
    session = await _get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(user.id) != session["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Return cached screenshot if still fresh
    cached = session.get("last_screenshot")
    if cached and time.time() - cached["ts"] < 1:
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
    result_future: asyncio.Future = asyncio.get_running_loop().create_future()
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


# ── Replay-Modus: record a step-by-step transcript (action + screenshot per
# step) of what happened in a session, for later review or skill authoring.
#
# This is the recording half only — turning a transcript into an actual
# reusable Skill (parameterizing steps, writing a SKILL.md) is a separate,
# larger piece and deliberately NOT built here yet; see todo.md.
# ──────────────────────────────────────────────────────────────────────────────

def _require_owned_session(session_id: str, user) -> dict:
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if str(user.id) != session["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    return session


async def _send_bridge_action(session: dict, action: str, timeout: float = 10.0) -> dict:
    """Send a bare action to the bridge and await its result."""
    if not session["bridge_connected"] or not session["bridge_ws"]:
        raise HTTPException(status_code=503, detail="Bridge not connected")
    cmd_id = uuid.uuid4().hex[:8]
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    session["pending_results"][cmd_id] = future
    try:
        await session["bridge_ws"].send_text(json.dumps({
            "type": "command", "id": cmd_id, "command": {"action": action, "params": {}},
        }))
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        session["pending_results"].pop(cmd_id, None)
        raise HTTPException(status_code=504, detail=f"Bridge timed out on {action}")


class RecordingStartRequest(BaseModel):
    # Also record what the HUMAN does at the machine (needs the input_capture
    # capability). Off by default — see the privacy note on InputRecorder.
    capture_human: bool = False


@router.post("/sessions/{session_id}/recording/start")
async def start_recording(
    session_id: str,
    req: RecordingStartRequest | None = None,
    user=Depends(require_auth),
):
    """Begin capturing a step-by-step transcript of this session.

    Records the agent's own actions by default; with capture_human the bridge
    additionally observes the user's clicks/keystrokes so a workflow can be
    demonstrated by hand.
    """
    session = _require_owned_session(session_id, user)
    capture_human = bool(req and req.capture_human)

    if capture_human:
        allowed: set[str] = session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)
        if not _action_allowed("start_input_capture", allowed):
            raise HTTPException(
                status_code=403,
                detail="Human input capture is disabled for this session — enable the "
                       "'input_capture' capability first.",
            )
        result = await _send_bridge_action(session, "start_input_capture")
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error") or "Bridge could not start input capture")

    session["recording"] = True
    session["recording_steps"] = []
    session["capture_human"] = capture_human
    return {"session_id": session_id, "recording": True, "capture_human": capture_human}


@router.post("/sessions/{session_id}/recording/stop")
async def stop_recording(session_id: str, user=Depends(require_auth)):
    """Stop capturing and return the full transcript (steps with screenshots)."""
    session = _require_owned_session(session_id, user)
    if session.get("capture_human"):
        try:
            await _send_bridge_action(session, "stop_input_capture")
        except HTTPException:
            # Bridge already gone — it stops capture itself on disconnect.
            pass
        session["capture_human"] = False
    session["recording"] = False
    steps = session.get("recording_steps", [])
    return {"session_id": session_id, "recording": False, "steps": steps, "step_count": len(steps)}


@router.get("/sessions/{session_id}/recording")
async def get_recording(session_id: str, user=Depends(require_auth)):
    """Peek at the transcript recorded so far without stopping the recording."""
    session = _require_owned_session(session_id, user)
    steps = session.get("recording_steps", [])
    return {
        "session_id": session_id,
        "recording": bool(session.get("recording")),
        "steps": steps,
        "step_count": len(steps),
    }


class SkillFromRecordingRequest(BaseModel):
    goal_hint: str = ""
    model: str | None = None


@router.post("/sessions/{session_id}/recording/to-skill", status_code=201)
async def recording_to_skill(
    session_id: str,
    req: SkillFromRecordingRequest,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Turn the recorded transcript into a reusable Skill (stored as DRAFT).

    Stops the recording first so the transcript is final. The heavy lifting
    (vision model reads the screenshots, writes parameterized prose steps)
    lives in services/replay_skill_service.py.
    """
    from app.services.replay_skill_service import ReplaySkillError, create_skill_from_recording

    session = _require_owned_session(session_id, user)
    session["recording"] = False
    steps = session.get("recording_steps", [])
    if not steps:
        raise HTTPException(status_code=400, detail="Nothing recorded yet — start a recording and perform some actions first.")

    try:
        skill = await create_skill_from_recording(
            db, steps,
            created_by=f"replay:{user.id}",
            goal_hint=req.goal_hint,
            model=req.model,
        )
    except ReplaySkillError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "skill_id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "status": skill.status.value,
        "step_count": len(steps),
    }


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

    # Restores from Redis when this process didn't create the session (restart,
    # deploy). Without this the bridge was told "create a new one" after every
    # orchestrator restart and the user had to re-enter a fresh session id.
    session = await _get_session(session_id)
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
    session["bridge_host"] = websocket.headers.get("host")
    # A fresh attach starts a fresh action budget — otherwise a session that
    # stays alive for days would eventually hit the cap and 429 forever.
    session["action_count"] = 0
    await _touch_session(session_id)
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
                logger.info(
                    "Bridge hello: caps=%s platform=%s version=%s",
                    msg.get("capabilities"),
                    msg.get("platform"),
                    msg.get("bridge_version"),
                )
                session["capabilities"] = msg.get("capabilities", [])
                session["platform"] = msg.get("platform", "unknown")
                session["bridge_version"] = msg.get("bridge_version")

            elif msg_type == "input_event":
                # Replay-Modus, human source: the bridge observed the USER
                # clicking/typing and pushed it up unsolicited. Same transcript
                # the agent's own actions land in, so skill authoring doesn't
                # care which source demonstrated the workflow.
                if session.get("recording"):
                    event = msg.get("event") or {}
                    if event.get("action"):
                        session["recording_steps"].append(event)

            elif msg_type == "pong":
                pass  # bridge_last_seen_at already updated above

    except WebSocketDisconnect:
        logger.info(f"Bridge disconnected for session {session_id}")
    finally:
        ping_task.cancel()
        session["bridge_connected"] = False
        session["bridge_ws"] = None
        session["bridge_host"] = None
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
