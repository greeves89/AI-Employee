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
#
# WICHTIG — die Kurznamen sind keine Kosmetik: Der MCP-Server
# (agent/mcp/computer-use-server.mjs) sendet `click`/`move`/`scroll`/
# `get_clipboard`/`set_clipboard`, der Codex-Weg (agent/app/tools/definitions.py)
# die langen `mouse_*`/`clipboard_*`. Die Bridge akzeptiert beide Schreibweisen
# seit jeher (`action in ("click", "mouse_click")`), NUR diese Liste kannte die
# kurzen nicht — und `_action_allowed` ist fail-closed. Ergebnis: fuer jeden
# Claude-Code-Agenten war Klicken, Scrollen und Element-Suchen mit 403 gesperrt,
# waehrend dieselben Faehigkeiten ueber Codex liefen. Das ist die Ursache hinter
# "Navigieren und Formulare ausfuellen ist noch nicht verlaesslich" in
# docs/AI-Employee-Features.md — nicht das Modell, sondern die API.
# test_bridge_action_names_are_allowed.py haelt beide Seiten ab jetzt zusammen.
CAPABILITY_GROUPS: dict[str, list[str]] = {
    "screenshots": ["screenshot", "get_mouse_position"],
    "mouse": ["mouse_move", "move", "mouse_click", "click", "mouse_scroll", "scroll", "drag"],
    "keyboard": ["key", "type", "hotkey"],
    # Elemente FINDEN statt auf Koordinaten zu raten — der Kern verlaesslicher
    # Bedienung. Beides war implementiert (Bridge + MCP-Werkzeug), aber hier nie
    # eingetragen und damit unerreichbar.
    "accessibility": ["ax_tree", "find_element", "wait_for_element"],
    "apps": ["open_app", "close_app", "open_url", "list_windows", "focus_window"],
    "clipboard": ["clipboard_read", "get_clipboard", "clipboard_write", "set_clipboard"],
    "shell": ["shell_run"],
    # Replay-Modus: observe the human's own clicks/keystrokes. Off by default
    # like shell — while active it sees everything typed on that machine.
    "input_capture": ["start_input_capture", "stop_input_capture"],
    # Voice-on-Desktop: das Mikrofon des Menschen. Wie input_capture nur zwischen
    # ausdruecklichem Start und Stopp — und aus demselben Grund default aus.
    "voice_capture": ["start_voice_capture", "stop_voice_capture"],
    # Browser im eigenen Profil (Phase 2). Default aus: er arbeitet in einer
    # angemeldeten Sitzung, das ist naeher an `shell` als an `screenshots`.
    "browser": [
        "browser_navigate", "browser_snapshot", "browser_click", "browser_fill",
        "browser_wait", "browser_capture", "browser_tabs", "browser_close",
    ],
    # ego lite: JS-Automation gegen die ECHTE, eingeloggte Browsersitzung des
    # Nutzers (Task Spaces erben den Login-Status) — bewusst das Gegenteil von
    # `browser` oben, das ein isoliertes eigenes Profil haelt. Deshalb wie
    # `shell` standardmaessig AUS: sonst bekaeme der Agent stillschweigend
    # Zugriff auf bereits angemeldete Konten. `ego_run` selbst hat keine
    # URL-Feinsteuerung (ein JS-Skript ist kein einzelnes Ziel) — die
    # diskreten Aktionen darunter sind das Gegenstueck zu `browser_*`, damit
    # kein Modell selbst JS schreiben muss; `ego_navigate` bekommt dieselbe
    # Domain-Freigabe wie `browser_navigate` (siehe _URL_SCOPED_ACTIONS unten).
    "ego_browser": [
        "ego_run", "ego_navigate", "ego_snapshot", "ego_click", "ego_fill",
        "ego_wait", "ego_capture", "ego_tabs", "ego_close",
    ],
    # Mitsehen (#1019): Die Bridge schaut VON SICH AUS auf den Bildschirm,
    # gleicht Regeln ab und schickt bei Mitdenken Bilder an den Agenten. Keine
    # Agenten-Aktion — der Agent kann damit nichts ausloesen, nur die Bridge
    # meldet. Default aus wie input_capture: Sie sieht alles, was sichtbar ist.
    "observe": [],
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


def _default_caps_from_policy(access_policy: dict | None) -> set[str]:
    """Pure helper so callers that already hold a loaded ``Agent`` (e.g.
    ``assign_agent``'s ownership check) don't need a second DB round-trip."""
    from app.core import autonomy_matrix as am

    caps = am.computer_use_default_capabilities(access_policy)
    return set(caps) if caps is not None else set(DEFAULT_ALLOWED_CAPABILITIES)


async def _agent_default_capabilities(agent_id: str, db: AsyncSession) -> set[str]:
    """Der dauerhafte Computer-Use-Default dieses Agenten (Issue #787 Punkt 1).

    Vorher gab es dafuer gar keinen dauerhaften Wert -- jede Session startete
    immer mit demselben Plattform-Default, egal ob der Agent laut Autonomie-
    Matrix ueberhaupt Shell-/System-Aktionen ausfuehren darf. Fehlt der Agent
    oder hat er keinen eigenen Wert gesetzt, gilt weiterhin der Plattform-
    Default -- unveraendertes Verhalten fuer jeden Agenten ohne diese neue
    Einstellung.
    """
    from app.models.agent import Agent

    agent = await db.get(Agent, agent_id)
    if not agent:
        return set(DEFAULT_ALLOWED_CAPABILITIES)
    return _default_caps_from_policy(agent.access_policy)


def _action_allowed(action: str, allowed: set[str]) -> bool:
    """Return True if the action is covered by at least one allowed capability group."""
    group = _ACTION_TO_GROUP.get(action)
    if group is None:
        # Unknown actions (e.g. future bridge commands) — block by default
        return False
    return group in allowed


# ── Scope-Listen: WELCHE Anwendung, WELCHE Adresse ────────────────────────────
#
# Die Capability-Gruppe sagt nur, OB der Agent Anwendungen bedienen darf. Sie
# sagt nicht, WELCHE. Genau das ist die Zusage an den Nutzer: "nur die, die ich
# freigebe".
#
# Durchgesetzt wird das HIER, serverseitig — nicht in der Tray-App. Die kennt
# zwar seit jeher ein Feld `allowed_paths`, zeigt es im Berechtigungs-Dialog an
# und speichert es lokal, aber es erreicht weder Bridge noch Server: eine
# Freigabe-Einstellung ohne jede Wirkung. Eine Oberflaeche, die Schutz zusagt
# und nichts durchsetzt, ist schlechter als gar keine — sie erzeugt Vertrauen,
# das nicht gedeckt ist.
#
# ``None`` heisst ausdruecklich "nicht eingeschraenkt" (bisheriges Verhalten,
# Bestandssitzungen brechen nicht). Eine LEERE Liste heisst "nichts erlaubt".

# Welcher Parameter traegt bei welcher Aktion den zu pruefenden Namen.
_APP_SCOPED_ACTIONS: dict[str, tuple[str, ...]] = {
    "open_app": ("app", "name"),
    "close_app": ("app", "name"),
    "focus_window": ("app", "name"),
}
_URL_SCOPED_ACTIONS: dict[str, tuple[str, ...]] = {
    "open_url": ("url",),
    "browser_navigate": ("url",),
    "ego_navigate": ("url",),
}


def _app_in_scope(app: str, allowed_apps: list[str] | None) -> bool:
    """Darf diese Anwendung bedient werden?

    Vergleich ohne Gross-/Kleinschreibung und ohne ``.app``/``.exe``-Endung —
    der Nutzer traegt "Excel" ein, das Modell schickt je nach Plattform
    "Excel.app" oder "EXCEL.EXE".
    """
    if allowed_apps is None:
        return True

    def norm(value: str) -> str:
        value = (value or "").strip().lower()
        for suffix in (".app", ".exe"):
            if value.endswith(suffix):
                value = value[: -len(suffix)]
        return value

    return norm(app) in {norm(a) for a in allowed_apps}


def _url_in_scope(url: str, allowed_domains: list[str] | None) -> bool:
    """Darf diese Adresse angesteuert werden?

    Verglichen wird der HOST, nicht die Zeichenkette: ``example.com`` erlaubt
    ``a.example.com``, aber ausdruecklich NICHT ``example.com.angreifer.tld``
    und auch nicht ``boeseexample.com``.
    """
    if allowed_domains is None:
        return True
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    for entry in allowed_domains:
        want = (entry or "").strip().lower().lstrip(".")
        if not want:
            continue
        if host == want or host.endswith("." + want):
            return True
    return False


def _scope_violation(action: str, params: dict, session: dict) -> str | None:
    """Fehlertext, wenn die Aktion ausserhalb der Freigabeliste liegt — sonst None."""
    for keys, allowed, kind in (
        (_APP_SCOPED_ACTIONS.get(action), session.get("allowed_apps"), "app"),
        (_URL_SCOPED_ACTIONS.get(action), session.get("allowed_domains"), "url"),
    ):
        if not keys:
            continue
        value = ""
        for key in keys:
            if params.get(key):
                value = str(params[key])
                break
        if not value:
            continue
        if kind == "app" and not _app_in_scope(value, allowed):
            return (f"Anwendung '{value}' ist für diese Sitzung nicht freigegeben. "
                    f"Freigegeben: {', '.join(allowed or []) or '(keine)'}")
        if kind == "url" and not _url_in_scope(value, allowed):
            return (f"Adresse '{value}' ist für diese Sitzung nicht freigegeben. "
                    f"Freigegeben: {', '.join(allowed or []) or '(keine)'}")
    return None


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
    "allowed_apps", "allowed_domains",
    "action_count", "last_activity_at", "platform", "bridge_version",
    "mitdenken", "deckel", "gestoppt",
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
        # None = nicht eingeschraenkt. Bewusst KEIN `or []` — eine leere Liste
        # ist eine Aussage ("nichts erlaubt") und darf nicht zu "alles" kippen.
        "allowed_apps": stored.get("allowed_apps"),
        "allowed_domains": stored.get("allowed_domains"),
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "agent_id": stored.get("agent_id"),
        "platform": stored.get("platform"),
        "bridge_version": stored.get("bridge_version"),
        "recording": False,
        "recording_steps": [],
        "capture_human": False,
        "mitdenken": bool(stored.get("mitdenken")),
        "deckel": stored.get("deckel"),
        # Ein Stopp ueberlebt den Neustart — sonst waere er nach jedem Deploy weg.
        "gestoppt": bool(stored.get("gestoppt")),
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
        "mitdenken": bool(s.get("mitdenken")),
        "deckel": _deckel(s),
    }


# ── Session Management ────────────────────────────────────────────────────────

class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    ws_url: str
    allowed_capabilities: list[str]


@router.post("/sessions", response_model=SessionCreateResponse)
async def create_session(
    user=Depends(require_auth),
    reuse: bool = True,
    agent_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Create a bridge session — or hand back the user's existing one.

    ``reuse`` (default on) returns the caller's most recent live session instead
    of minting a new id. Without it every visit to the Computer-Use tab produced
    a fresh id and the bridge had to be reconfigured by hand each time; with it
    the bridge keeps working across restarts and page reloads.

    ``agent_id``, when given, seeds the session with the INTERSECTION of the
    platform default and that agent's durable Computer-Use default (Issue
    #787 Punkt 1) instead of the bare platform default — a read-only agent
    never starts a session with mouse/keyboard just because the platform
    default includes them.
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

    if agent_id:
        from sqlalchemy import select
        from app.models.agent import Agent
        owned_agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
        if not owned_agent or str(owned_agent.user_id) != str(user.id):
            raise HTTPException(status_code=404, detail="Agent not found or not yours")

    session_id = uuid.uuid4().hex[:12]
    allowed = set(DEFAULT_ALLOWED_CAPABILITIES)
    if agent_id:
        allowed &= _default_caps_from_policy(owned_agent.access_policy)
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
        # None = keine Einschraenkung (bisheriges Verhalten). Der Nutzer engt
        # das im Berechtigungs-Dialog auf einzelne Anwendungen/Adressen ein.
        "allowed_apps": None,
        "allowed_domains": None,
        "last_disconnected_at": None,
        "bridge_last_seen_at": None,
        "bridge_host": None,
        "agent_id": agent_id,
        # Replay-Modus: while recording, every screen-changing action is
        # captured as a step (action + params + a screenshot taken right
        # after it) — see /recording/start|stop below.
        "recording": False,
        "recording_steps": [],
        "capture_human": False,
        # Mitdenker (#1019): Bilder an den Agenten nur, wenn eingeschaltet —
        # die Capability ``observe`` allein erlaubt nur den lokalen Regelabgleich.
        "mitdenken": False,
        "deckel": None,
    }
    await _persist_session(session_id)
    logger.info(f"Created computer-use session {scrub_log(session_id)} for user {user.id}")
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
    # None = Feld nicht mitgeschickt → bestehende Einstellung bleibt. So kann
    # die Tray-App weiterhin nur die Gruppen patchen, ohne die Freigabelisten
    # versehentlich auf "alles" zurueckzusetzen.
    allowed_apps: list[str] | None = None
    allowed_domains: list[str] | None = None
    # Ausdrueckliches Aufheben einer Einschraenkung — sonst gaebe es keinen Weg
    # zurueck von "nur diese drei" nach "alle", weil None schon "unveraendert"
    # bedeutet.
    clear_app_scope: bool = False
    clear_domain_scope: bool = False
    # Mitdenker (#1019). None = unveraendert, wie bei den Freigabelisten.
    mitdenken: bool | None = None
    deckel: int | None = None


@router.patch("/sessions/{session_id}/capabilities")
async def update_capabilities(
    session_id: str,
    req: CapabilityUpdate,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Update which capability groups are allowed for this session.

    When an agent is assigned to this session, the requested set may never
    exceed that agent's durable Computer-Use default (Issue #787 Punkt 1) —
    makes "Session ⊆ Agenten-Default" an enforced invariant, not just a
    seeding convenience honored only at session creation.
    """
    session = await _get_session(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")

    unknown = set(req.allowed_capabilities) - set(CAPABILITY_GROUPS.keys())
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown capability groups: {sorted(unknown)}")

    if session.get("agent_id"):
        agent_default = await _agent_default_capabilities(session["agent_id"], db)
        # Mitsehen ist keine Faehigkeit des Agenten, sondern die Entscheidung des
        # Menschen ueber seinen Rechner (#1019) — der Agenten-Standard begrenzt sie nicht.
        over_cap = set(req.allowed_capabilities) - agent_default - {OBSERVE_CAP}
        if over_cap:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Fähigkeiten außerhalb des Agenten-Standards: {sorted(over_cap)}. "
                    "Der zugewiesene Agent erlaubt höchstens "
                    f"{sorted(agent_default)}."
                ),
            )

    session["allowed_capabilities"] = set(req.allowed_capabilities)
    if req.clear_app_scope:
        session["allowed_apps"] = None
    elif req.allowed_apps is not None:
        session["allowed_apps"] = list(req.allowed_apps)
    if req.clear_domain_scope:
        session["allowed_domains"] = None
    elif req.allowed_domains is not None:
        session["allowed_domains"] = list(req.allowed_domains)
    if req.mitdenken is not None:
        session["mitdenken"] = bool(req.mitdenken)
    if req.deckel is not None:
        from app.core import bridge_mitdenker as bm
        session["deckel"] = bm.deckel_lesen(req.deckel)

    logger.info(f"Session {scrub_log(session_id)}: capabilities updated to {scrub_log(sorted(req.allowed_capabilities))}")
    await _persist_session(session_id)
    antwort = {
        "session_id": session_id,
        "allowed_capabilities": sorted(session["allowed_capabilities"]),
        "allowed_apps": session.get("allowed_apps"),
        "allowed_domains": session.get("allowed_domains"),
        "mitdenken": bool(session.get("mitdenken")),
        "deckel": _deckel(session),
    }
    await _beobachter_konfig_senden(session_id)
    return antwort


class AgentAssignment(BaseModel):
    agent_id: str | None = None


@router.patch("/sessions/{session_id}/agent")
async def assign_agent(
    session_id: str,
    req: AgentAssignment,
    user=Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Assign (or unassign) an agent to this session. Only that agent may then send commands.

    Assigning an agent also caps this session's ``allowed_capabilities`` down
    to that agent's durable Computer-Use default (Issue #787 Punkt 1) — a
    session created before any agent was attached (today's usual flow) may
    otherwise carry the bare platform default, wider than what this agent
    should ever get.
    """
    session = await _get_session(session_id)
    if not session or session["user_id"] != str(user.id):
        raise HTTPException(status_code=404, detail="Session not found")

    if req.agent_id is not None:
        from sqlalchemy import select
        from app.models.agent import Agent
        agent = await db.scalar(select(Agent).where(Agent.id == req.agent_id))
        if not agent or str(agent.user_id) != str(user.id):
            raise HTTPException(status_code=404, detail="Agent not found or not yours")
        agent_default = _default_caps_from_policy(agent.access_policy)
        bisher = set(session.get("allowed_capabilities") or [])
        # Mitsehen bleibt, wie der Mensch es eingestellt hat (siehe update_capabilities).
        session["allowed_capabilities"] = (bisher & agent_default) | (bisher & {OBSERVE_CAP})

    session["agent_id"] = req.agent_id
    logger.info(f"Session {scrub_log(session_id)}: agent_id set to {scrub_log(req.agent_id)}")
    await _persist_session(session_id)
    # Mitdenken haengt am Agenten der Sitzung — die Bridge muss es sofort wissen.
    await _beobachter_konfig_senden(session_id)
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

    # Mitdenker (#1019): Der Mensch hat im Overlay „Stopp“ gedrueckt. Das gilt,
    # bis er dort wieder freigibt oder ein neues Angebot annimmt — ein Agent, der
    # einfach weiterklickt, waere kein Stopp.
    if session.get("gestoppt"):
        raise HTTPException(
            status_code=409,
            detail="Vom Menschen gestoppt (Stopp im Overlay). Nicht weitermachen — erst nachfragen.",
        )

    # Capability check — enforce server-side
    allowed: set[str] = session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)
    if not _action_allowed(req.action, allowed):
        group = _ACTION_TO_GROUP.get(req.action, "unknown")
        raise HTTPException(
            status_code=403,
            detail=f"Action '{req.action}' is not permitted (capability group '{group}' is disabled for this session).",
        )

    # Die Gruppe erlaubt die ART der Aktion, die Freigabeliste das ZIEL.
    violation = _scope_violation(req.action, req.params or {}, session)
    if violation:
        raise HTTPException(status_code=403, detail=violation)

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
    await _schritt_melden(session, req.action)

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
        raise HTTPException(status_code=403, detail="Kein Zugriff")

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
        raise HTTPException(status_code=403, detail="Kein Zugriff")

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
        raise HTTPException(status_code=403, detail="Kein Zugriff")
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


# ── Mitdenker (#1019): Regeln, Beobachtungen, Overlay ─────────────────────────
#
# Die Bridge sieht mit (Capability ``observe``), gleicht Regeln LOKAL ab und
# meldet Treffer (``rule_hit``); mit eingeschaltetem Mitdenken schickt sie bei
# deutlicher Bildaenderung ein Bild (``observation``), und der Agent der Sitzung
# entscheidet, ob er etwas anbietet. Beides endet als ``vorschlag`` im Overlay;
# erst ein Ja gibt dem Agenten den Auftrag. Der Agent bekommt alles ueber seinen
# normalen Chat (``agent_chat_bridge``) — gleich in jeder Laufzeit, mit der
# Budgetpruefung jeder Chatnachricht. Fachlogik: ``core/bridge_mitdenker``.

OBSERVE_CAP = "observe"
_BILDTYPEN = {"image/jpeg", "image/png"}
# ~5 MB Bild als base64 — dieselbe Grenze wie fuer Bilder im Chat.
_MAX_BILD_B64 = 7_000_000
# Ein unbeantwortetes Angebot verfaellt nach 10 Minuten.
_VORSCHLAG_LEBT_SECS = 600
# Solange ein Angebot offen ist, denkt der Agent nicht schon das naechste aus.
_VORSCHLAG_SPERRT_SECS = 120
# Eine Pause von einer Minute beginnt im Overlay einen neuen Schrittzaehler.
_LAUF_PAUSE_SECS = 60
# Dieselbe Regel loest serverseitig hoechstens einmal je Minute ein Angebot aus.
_REGEL_SERVER_RUHE_SECS = 60


def _deckel(session: dict) -> int:
    from app.core import bridge_mitdenker as bm

    wert = session.get("deckel")
    return bm.STANDARD_DECKEL if wert is None else bm.deckel_lesen(wert)


def _beobachten_erlaubt(session: dict) -> bool:
    return OBSERVE_CAP in (session.get("allowed_capabilities") or set())


def _hintergrund(session: dict, coro) -> None:
    """Aufgabe starten und festhalten — sonst kann der Garbage Collector sie mitten
    im Lauf einsammeln."""
    aufgaben = session.setdefault("_aufgaben", set())
    task = asyncio.create_task(coro)
    aufgaben.add(task)
    task.add_done_callback(aufgaben.discard)


async def _an_bridge(session: dict, nachricht: dict) -> bool:
    """Nachricht an die verbundene Bridge. Ohne Verbindung: False, nie ein Fehler —
    eine aeltere Bridge ignoriert unbekannte Typen ohnehin."""
    ws = session.get("bridge_ws")
    if not session.get("bridge_connected") or ws is None:
        return False
    try:
        await ws.send_text(json.dumps(nachricht))
        return True
    except Exception:  # noqa: BLE001 — Verbindung weg; die Schleife raeumt auf
        logger.debug("Overlay-Nachricht nicht zustellbar", exc_info=True)
        return False


async def _schritt_melden(session: dict, action: str) -> None:
    """Schrittzaehler im Overlay: Der Mensch sieht, DASS und WAS der Agent gerade
    auf seinem Rechner tut — bei jeder Aktion, nicht nur nach einem Angebot."""
    jetzt = time.time()
    lauf = session.get("lauf") or {}
    if jetzt - float(lauf.get("zuletzt") or 0) > _LAUF_PAUSE_SECS:
        lauf = {"schritte": 0}
    lauf["schritte"] = int(lauf.get("schritte") or 0) + 1
    lauf["zuletzt"] = jetzt
    session["lauf"] = lauf
    await _an_bridge(session, {
        "type": "overlay_status", "zustand": "arbeitet",
        "schritte": lauf["schritte"], "aktion": action,
    })


# Wie lange das Ergebnis der Versionspruefung gilt.
_ABBILD_PRUEFUNG_SECS = 300


async def _agent_kann_ansehen(session: dict) -> bool:
    """Kennt das Abbild des Sitzungs-Agenten den Nur-ansehen-Zug? Sonst bekommt er
    keine Bilder — ein aelteres Abbild saehe sie mit allen Werkzeugen."""
    from app.core import bridge_mitdenker as bm
    from app.db.session import async_session_factory
    from app.models.agent import Agent

    agent_id = session.get("agent_id")
    if not agent_id:
        return False
    gemerkt = session.get("_abbild_ok")
    if gemerkt and gemerkt[0] == agent_id and time.time() - gemerkt[1] < _ABBILD_PRUEFUNG_SECS:
        return gemerkt[2]
    try:
        async with async_session_factory() as db:
            agent = await db.get(Agent, str(agent_id))
        ok = bool(agent) and bm.kann_nur_ansehen((agent.config or {}).get("agent_version"))
    except Exception:  # noqa: BLE001 — im Zweifel keine Bilder
        logger.debug("Abbildversion nicht lesbar", exc_info=True)
        ok = False
    session["_abbild_ok"] = (agent_id, time.time(), ok)
    return ok


async def _regeln_fuer_bridge(user_id: str) -> list[dict]:
    from app.core import bridge_mitdenker as bm
    from app.db.session import async_session_factory

    async with async_session_factory() as db:
        return [bm.regel_fuer_bridge(r) for r in await bm.regeln_laden(db, user_id, nur_aktive=True)]


async def _beobachter_konfig_senden(session_id: str) -> None:
    """Was die Bridge beobachten darf und welche Regeln sie abgleicht."""
    session = _sessions.get(session_id)
    if not session or not session.get("bridge_connected"):
        return
    beobachten = _beobachten_erlaubt(session)
    regeln: list[dict] = []
    if beobachten:
        try:
            regeln = await _regeln_fuer_bridge(session["user_id"])
        except Exception:  # noqa: BLE001 — ohne Regeln beobachtet sie trotzdem
            logger.warning("Bridge-Regeln nicht ladbar", exc_info=True)
    mitdenken = beobachten and bool(session.get("mitdenken")) and bool(session.get("agent_id"))
    if mitdenken and not await _agent_kann_ansehen(session):
        mitdenken = False
        await _an_bridge(session, {
            "type": "overlay_status", "zustand": "hinweis",
            "text": "Mitdenken braucht ein aktuelles Abbild des Agenten — bitte den Agenten aktualisieren.",
        })
    await _an_bridge(session, {
        "type": "observer_config",
        "beobachten": beobachten,
        # Bilder schickt die Bridge nur, wenn sie auch jemand ansieht.
        "mitdenken": mitdenken,
        "regeln": regeln,
        "gestoppt": bool(session.get("gestoppt")),
        # Fuer den Knopf „Chat“ im Overlay — der eigene Agent des Menschen.
        "agent_id": session.get("agent_id"),
    })


async def beobachter_konfig_an_nutzer(user_id: str) -> None:
    """Regeln geaendert → alle verbundenen Bridges dieses Nutzers sofort nachziehen."""
    for sid, s in list(_sessions.items()):
        if s.get("user_id") == str(user_id) and s.get("bridge_connected"):
            await _beobachter_konfig_senden(sid)


def _offenes_angebot(session: dict) -> bool:
    jetzt = time.time()
    return any(jetzt - v["ts"] < _VORSCHLAG_SPERRT_SECS for v in (session.get("vorschlaege") or {}).values())


async def _vorschlag_zeigen(session: dict, *, angebot: str, auftrag: str, agent_id: str,
                            quelle: str, app: str, titel: str, regel_id: int | None = None) -> str | None:
    jetzt = time.time()
    offen = {k: v for k, v in (session.get("vorschlaege") or {}).items()
             if jetzt - v["ts"] < _VORSCHLAG_LEBT_SECS}
    vid = uuid.uuid4().hex[:10]
    offen[vid] = {"angebot": angebot, "auftrag": auftrag, "agent_id": agent_id,
                  "quelle": quelle, "app": app, "titel": titel, "ts": jetzt, "regel_id": regel_id}
    session["vorschlaege"] = dict(sorted(offen.items(), key=lambda kv: kv[1]["ts"])[-5:])
    # Das Ja gilt dem, was der Agent dann TUT — deshalb steht der Auftrag mit im
    # Overlay, nicht nur die freundliche Frage davor.
    # Vollstaendig, nie gekuerzt — Auftraege sind auf bm.MAX_AUFTRAG begrenzt.
    if await _an_bridge(session, {"type": "vorschlag", "id": vid, "text": angebot,
                                  "auftrag": auftrag, "quelle": quelle}):
        return vid
    session["vorschlaege"].pop(vid, None)
    return None


def _blick_lesen(msg: dict) -> dict:
    bild = msg.get("bild_b64") or ""
    media_type = msg.get("media_type") or "image/jpeg"
    if not isinstance(bild, str) or media_type not in _BILDTYPEN or len(bild) > _MAX_BILD_B64:
        bild = ""
    elif bild:
        import base64
        import binascii
        try:
            base64.b64decode(bild, validate=True)
        except (binascii.Error, ValueError):
            bild = ""
    return {
        "app": " ".join(str(msg.get("app") or "").split())[:120],
        "titel": " ".join(str(msg.get("titel") or "").split())[:300],
        "bild": bild,
        "media_type": media_type,
        "ts": time.time(),
    }


async def _beobachtung(session_id: str, session: dict, msg: dict) -> None:
    from app.core import bridge_mitdenker as bm

    if not _beobachten_erlaubt(session):
        return
    blick = _blick_lesen(msg)
    session["letzter_blick"] = blick
    if not (session.get("mitdenken") and session.get("agent_id") and blick["bild"]):
        return
    if session.get("mitdenken_laeuft") or session.get("auftrag_laeuft") or session.get("gestoppt"):
        return
    if _offenes_angebot(session):
        return
    if not await _agent_kann_ansehen(session):
        return
    if not await bm.lauf_erlaubt(_redis.client if _redis else None, session["user_id"], _deckel(session)):
        heute = time.strftime("%Y-%m-%d")
        if session.get("deckel_gemeldet") != heute:
            session["deckel_gemeldet"] = heute
            await _an_bridge(session, {
                "type": "overlay_status", "zustand": "hinweis",
                "text": "Tagesdeckel erreicht — Mitdenken pausiert bis morgen. Regeln wirken weiter.",
            })
        return
    session["mitdenken_laeuft"] = True
    _hintergrund(session, _mitdenken(session_id, blick))


async def _mitdenken(session_id: str, blick: dict) -> None:
    from app.core import bridge_mitdenker as bm
    from app.services.agent_chat_bridge import ask_agent_via_chat

    session = _sessions.get(session_id)
    if not session:
        return
    try:
        agent_id = str(session.get("agent_id") or "")
        antwort = await ask_agent_via_chat(
            _redis, agent_id, bm.beobachter_anweisung(blick["app"], blick["titel"]),
            source="bridge", timeout=180.0,
            # Eigener Faden: Die Blicke sollen den Hauptchat nicht fluten.
            chat_session_id="bridge-beobachter",
            images=[{"media_type": blick["media_type"], "data": blick["bild"]}],
            nur_ansehen=True,
        )
        angebot = bm.angebot_lesen(antwort)
        if angebot and not session.get("gestoppt"):
            await _vorschlag_zeigen(session, angebot=angebot["angebot"], auftrag=angebot["auftrag"],
                                    agent_id=agent_id, quelle="mitdenken",
                                    app=blick["app"], titel=blick["titel"])
    except Exception:  # noqa: BLE001 — ein Blick, der scheitert, ist kein Fehler fuer den Menschen
        logger.warning("[Mitdenker] Blick fehlgeschlagen", exc_info=True)
    finally:
        session["mitdenken_laeuft"] = False


async def _regel_treffer(session: dict, msg: dict) -> None:
    from datetime import datetime, timezone

    from app.core import bridge_mitdenker as bm
    from app.db.session import async_session_factory

    if not _beobachten_erlaubt(session) or session.get("gestoppt") or session.get("auftrag_laeuft"):
        return
    try:
        regel_id = int(msg.get("regel_id"))
    except (TypeError, ValueError):
        return
    if not 0 < regel_id < 2**31:
        return
    # Eine Bridge, die denselben Treffer im Sekundentakt meldet, schreibt nicht
    # jedes Mal in die Datenbank.
    zuletzt = session.setdefault("_regel_zuletzt", {})
    if time.time() - zuletzt.get(regel_id, 0) < _REGEL_SERVER_RUHE_SECS:
        return
    app = " ".join(str(msg.get("app") or "").split())[:120]
    titel = " ".join(str(msg.get("titel") or "").split())[:300]
    async with async_session_factory() as db:
        r = await bm.regel_holen(db, session["user_id"], regel_id)
        # Nachpruefen statt glauben: Die Bridge meldet, der Server entscheidet.
        if r is None or not bm.regel_passt(bm.regel_dict(r), app, titel):
            return
        agent_id = r.agent_id or session.get("agent_id")
        if agent_id and not await bm._agent_gehoert(db, session["user_id"], str(agent_id)):
            return
        if not agent_id:
            await _an_bridge(session, {
                "type": "overlay_status", "zustand": "hinweis",
                "text": "Regel passt, aber der Bridge ist kein Agent zugeordnet.",
            })
            return
        # Erst ein nachgeprueft echter Treffer zaehlt fuer die Ruhezeit — ein
        # falscher darf den naechsten echten nicht blockieren.
        zuletzt[regel_id] = time.time()
        r.treffer = int(r.treffer or 0) + 1
        r.zuletzt_at = datetime.now(timezone.utc)
        angebot, auftrag = r.angebot, r.auftrag
        await db.commit()
    await _vorschlag_zeigen(session, angebot=angebot, auftrag=auftrag, agent_id=str(agent_id),
                            quelle="regel", app=app, titel=titel, regel_id=regel_id)


async def _vorschlag_antwort(session_id: str, session: dict, msg: dict) -> None:
    vorschlag = (session.get("vorschlaege") or {}).pop(str(msg.get("id") or ""), None)
    if vorschlag is None or str(msg.get("antwort") or "").strip().lower() != "ja":
        return
    if session.get("auftrag_laeuft"):
        await _an_bridge(session, {"type": "overlay_status", "zustand": "hinweis",
                                   "text": "Es läuft schon ein Auftrag — erst den abwarten oder stoppen."})
        return
    zusatz = " ".join(str(msg.get("text") or "").split())[:1000]
    # Ein Ja ist auch die Freigabe nach einem Stopp.
    session["gestoppt"] = False
    session["auftrag_laeuft"] = True
    session["auftrag_agent"] = vorschlag["agent_id"]
    session["lauf"] = {"schritte": 0, "zuletzt": time.time()}
    await _an_bridge(session, {"type": "overlay_status", "zustand": "arbeitet", "schritte": 0,
                               "text": "Agent übernimmt …"})
    _hintergrund(session, _auftrag_ausfuehren(session_id, vorschlag, zusatz))


async def _auftrag_ausfuehren(session_id: str, vorschlag: dict, zusatz: str) -> None:
    from app.core import bridge_mitdenker as bm
    from app.services.agent_chat_bridge import ask_agent_via_chat

    session = _sessions.get(session_id)
    if not session:
        return
    try:
        blick = session.get("letzter_blick") or {}
        bilder: list[dict] = []
        if blick.get("bild") and time.time() - float(blick.get("ts") or 0) < 30:
            bilder = [{"media_type": blick["media_type"], "data": blick["bild"]}]
        else:
            frisch = await _refresh_screenshot_cache(session_id)
            if frisch:
                bilder = [{"media_type": "image/png", "data": frisch}]
        antwort = await ask_agent_via_chat(
            _redis, vorschlag["agent_id"],
            bm.auftrag_text(vorschlag["auftrag"], vorschlag["angebot"], vorschlag["app"],
                            vorschlag["titel"], zusatz),
            source="bridge", timeout=900.0, chat_session_id="bridge", images=bilder,
        )
        fehler = antwort.startswith("[Fehler")
        await _an_bridge(session, {
            "type": "overlay_status", "zustand": "fehler" if fehler else "fertig",
            "schritte": int((session.get("lauf") or {}).get("schritte") or 0),
            "text": (antwort or "Fertig.")[:600],
        })
    except Exception:  # noqa: BLE001
        logger.warning("[Mitdenker] Auftrag fehlgeschlagen", exc_info=True)
        await _an_bridge(session, {"type": "overlay_status", "zustand": "fehler",
                                   "text": "Der Auftrag ist fehlgeschlagen."})
    finally:
        session["auftrag_laeuft"] = False


async def _overlay_stopp(session_id: str, session: dict) -> None:
    session["gestoppt"] = True
    session["vorschlaege"] = {}
    await _persist_session(session_id)
    # Nicht nur den Rechner sperren, auch den laufenden Zug des Agenten abbrechen —
    # derselbe Kanal wie der Stopp-Knopf im Web-Chat.
    for agent_id in {session.get("agent_id"), session.get("auftrag_agent")} - {None, ""}:
        try:
            if _redis is not None and _redis.client is not None:
                await _redis.client.publish(f"agent:{agent_id}:chat:cancel", "stop")
        except Exception:  # noqa: BLE001 — die Sperre oben gilt trotzdem
            logger.warning("Stopp an den Agenten nicht zustellbar", exc_info=True)
    await _an_bridge(session, {"type": "overlay_status", "zustand": "gestoppt",
                               "text": "Gestoppt. Der Agent darf nichts mehr auf diesem Rechner tun, bis du freigibst."})


async def _overlay_weiter(session_id: str, session: dict) -> None:
    session["gestoppt"] = False
    await _persist_session(session_id)
    await _an_bridge(session, {"type": "overlay_status", "zustand": "bereit", "text": "Freigegeben."})


def _angebote_der_regel_verwerfen(user_id: str, regel_id: int) -> None:
    """Regel geloescht oder ausgeschaltet: ihr offenes Angebot darf kein Ja mehr bekommen."""
    for s in list(_sessions.values()):
        if s.get("user_id") != str(user_id):
            continue
        offen = s.get("vorschlaege") or {}
        for vid in [k for k, v in offen.items() if v.get("regel_id") == regel_id]:
            offen.pop(vid, None)


class RegelEingabe(BaseModel):
    name: str | None = None
    wenn_app: str | None = None
    wenn_titel: str | None = None
    angebot: str | None = None
    auftrag: str | None = None
    agent_id: str | None = None
    aktiv: bool | None = None


async def _nutzer_des_rufers(caller, db: AsyncSession) -> str:
    user_id = await _resolve_caller_user_id(caller, db)
    if not user_id:
        raise HTTPException(status_code=403, detail="Cannot verify ownership — no user_id")
    return user_id


@router.get("/regeln")
async def regeln_auflisten(caller=Depends(require_auth_or_agent), db: AsyncSession = Depends(get_db)):
    """Die Regeln des Menschen (ein Agent sieht die seines Menschen)."""
    from app.core import bridge_mitdenker as bm

    user_id = await _nutzer_des_rufers(caller, db)
    regeln = await bm.regeln_laden(db, user_id)
    return {
        "regeln": [bm.regel_dict(r) for r in regeln],
        "laeufe_heute": await bm.laeufe_heute(_redis.client if _redis else None, user_id),
    }


@router.post("/regeln", status_code=201)
async def regel_anlegen(body: RegelEingabe, caller=Depends(require_auth_or_agent),
                        db: AsyncSession = Depends(get_db)):
    from app.core import bridge_mitdenker as bm

    user_id = await _nutzer_des_rufers(caller, db)
    felder = body.model_dump(exclude_none=True)
    von = "nutzer"
    if is_agent_principal(caller):
        # Ein Agent legt Regeln nur fuer sich selbst an, nie fuer andere Agenten.
        von = f"agent:{caller.id}"
        felder["agent_id"] = str(caller.id)
    try:
        r = await bm.regel_anlegen(db, user_id, felder, von=von)
    except bm.RegelFehler as e:
        raise HTTPException(status_code=422, detail=str(e))
    await db.commit()
    await db.refresh(r)
    await beobachter_konfig_an_nutzer(user_id)
    return bm.regel_dict(r)


@router.patch("/regeln/{regel_id}")
async def regel_aendern(regel_id: int, body: RegelEingabe, user=Depends(require_auth),
                        db: AsyncSession = Depends(get_db)):
    from app.core import bridge_mitdenker as bm

    try:
        r = await bm.regel_aendern(db, str(user.id), regel_id, body.model_dump(exclude_unset=True))
    except bm.RegelFehler as e:
        raise HTTPException(status_code=422, detail=str(e))
    if r is None:
        raise HTTPException(status_code=404, detail="Regel nicht gefunden.")
    await db.commit()
    await db.refresh(r)
    # Geaendert ist geaendert: ein offenes Angebot mit dem alten Auftrag verfaellt.
    _angebote_der_regel_verwerfen(str(user.id), regel_id)
    await beobachter_konfig_an_nutzer(str(user.id))
    return bm.regel_dict(r)


@router.delete("/regeln/{regel_id}")
async def regel_loeschen(regel_id: int, caller=Depends(require_auth_or_agent),
                         db: AsyncSession = Depends(get_db)):
    from app.core import bridge_mitdenker as bm

    user_id = await _nutzer_des_rufers(caller, db)
    r = await bm.regel_holen(db, user_id, regel_id)
    # Ein Agent loescht nur, was er selbst angelegt hat — die Regeln des Menschen
    # gehoeren dem Menschen.
    if r is None or (is_agent_principal(caller) and r.von != f"agent:{caller.id}"):
        raise HTTPException(status_code=404, detail="Regel nicht gefunden.")
    await db.delete(r)
    await db.commit()
    _angebote_der_regel_verwerfen(user_id, regel_id)
    await beobachter_konfig_an_nutzer(user_id)
    return {"ok": True}


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
    logger.info(f"Bridge connected for session {scrub_log(session_id)} (user {scrub_log(user_id)})")

    await websocket.send_text(json.dumps({
        "type": "session_info",
        "session_id": session_id,
        "allowed_capabilities": sorted(session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)),
    }))
    await _beobachter_konfig_senden(session_id)

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
                    scrub_log(msg.get("capabilities")),
                    scrub_log(msg.get("platform")),
                    scrub_log(msg.get("bridge_version")),
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

            elif msg_type == "observation":
                await _beobachtung(session_id, session, msg)

            elif msg_type == "rule_hit":
                await _regel_treffer(session, msg)

            elif msg_type == "vorschlag_antwort":
                await _vorschlag_antwort(session_id, session, msg)

            elif msg_type == "overlay_stopp":
                await _overlay_stopp(session_id, session)

            elif msg_type == "overlay_weiter":
                await _overlay_weiter(session_id, session)

            elif msg_type == "pong":
                pass  # bridge_last_seen_at already updated above

    except WebSocketDisconnect:
        logger.info(f"Bridge disconnected for session {scrub_log(session_id)}")
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
    except Exception:
        return None
    # Wie get_current_user: nur Zugangstoken (kein Refresh-/Zwischen-Token),
    # aktives, freigeschaltetes Konto, Sitzung nicht widerrufen (token_version).
    if payload.get("type") != "access":
        return None
    uid = str(payload.get("sub") or "")
    if not uid:
        return None
    from app.db.session import async_session_factory
    from app.models.user import User

    async with async_session_factory() as db:
        user = await db.get(User, uid)
    if not user or not user.is_active or not getattr(user, "approved", True):
        return None
    if payload.get("tv", 0) != user.token_version:
        return None
    return uid
