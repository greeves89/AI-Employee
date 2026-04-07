import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, Request

from app.core.stream_manager import StreamManager
from app.db.session import async_session_factory
from app.dependencies import get_current_user_ws, require_auth
from app.models.chat_message import ChatMessage
from app.security.agent_guard import chat_rate_limiter, check_chat_message, notify_security_block
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])

# Global stream manager and redis - initialized in main.py lifespan
stream_manager: StreamManager | None = None
_redis: RedisService | None = None
_docker: DockerService | None = None


def init_stream_manager(redis: RedisService, docker: DockerService | None = None) -> StreamManager:
    global stream_manager, _redis, _docker
    _redis = redis
    _docker = docker
    stream_manager = StreamManager(redis)
    return stream_manager


@router.post("/ticket")
async def create_ws_ticket(request: Request, user=Depends(require_auth)):
    """Create a short-lived one-time ticket for WebSocket authentication.

    The ticket is stored in Redis with a 30-second TTL and can only be used once.
    This avoids putting long-lived JWTs in WebSocket URL query parameters.
    """
    redis = _redis
    if not redis or not redis.client:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Redis not available")
    ticket_id = uuid.uuid4().hex
    await redis.client.setex(f"ws:ticket:{ticket_id}", 30, str(user.id))
    return {"ticket": ticket_id}


async def _authenticate_ws(websocket: WebSocket, token: str | None = None, ticket: str | None = None) -> bool:
    """Authenticate a WebSocket connection.

    Preferred: ticket= (one-time, from POST /ws/ticket, 30s TTL)
    Legacy: token= (JWT in URL -- deprecated, logged as warning)

    Returns True if authenticated, False if connection was rejected.
    In setup mode (no users), always returns True.
    """
    # Try ticket-based auth first (preferred)
    if ticket and _redis and _redis.client:
        user_id = await _redis.client.getdel(f"ws:ticket:{ticket}")
        if user_id:
            return True
        await websocket.close(code=4001, reason="Invalid or expired ticket")
        return False

    # Legacy: JWT token in URL (deprecated)
    if token:
        logger.warning("WebSocket using legacy token= param — migrate to ticket-based auth")

    try:
        async with async_session_factory() as db:
            user = await get_current_user_ws(token, db)
            if not user:
                await websocket.close(code=4001, reason="Invalid or expired token")
                return False
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return False

    return True


@router.websocket("/agents/{agent_id}/logs")
async def ws_agent_logs(websocket: WebSocket, agent_id: str, token: str | None = Query(None), ticket: str | None = Query(None)):
    if not stream_manager:
        await websocket.close(code=1011, reason="Stream manager not initialized")
        return

    if not await _authenticate_ws(websocket, token=token, ticket=ticket):
        return

    try:
        await stream_manager.stream_agent_logs(websocket, agent_id)
    except WebSocketDisconnect:
        pass


@router.websocket("/agents/{agent_id}/chat")
async def ws_agent_chat(websocket: WebSocket, agent_id: str, token: str | None = Query(None), ticket: str | None = Query(None)):
    """Bidirectional WebSocket for chatting with an agent.

    Client sends: {"text": "Hello"} or {"text": "/reset"}
    Server sends: {"type": "text", "data": {"text": "..."}, ...} (streamed)
    Server sends: {"type": "done", "data": {...}} when response complete
    """
    if not _redis or not _redis.client:
        await websocket.close(code=1011, reason="Redis not initialized")
        return

    # Authenticate via ticket (preferred) or legacy JWT token
    if not await _authenticate_ws(websocket, token=token, ticket=ticket):
        return

    # Check if agent container is alive before accepting
    if _docker:
        from app.models.agent import Agent
        from sqlalchemy import select

        try:
            async with async_session_factory() as db:
                result = await db.execute(select(Agent).where(Agent.id == agent_id))
                agent = result.scalar_one_or_none()
                if not agent:
                    await websocket.close(code=4004, reason="Agent not found")
                    return
                if not agent.container_id:
                    await websocket.close(code=4010, reason="Agent has no container")
                    return
                status = _docker.get_container_status(agent.container_id)
                if status not in ("running", "created"):
                    await websocket.close(code=4010, reason=f"Agent container is {status}")
                    return
        except Exception:
            pass  # Allow connection attempt if check fails

    await websocket.accept()

    # Subscribe to agent's chat response channel
    channel = f"agent:{agent_id}:chat:response"
    pubsub = await _redis.subscribe(channel)

    # Track streaming assistant responses for persistence
    _streaming_responses: dict[str, dict] = {}
    # Track seen tool_use_ids to prevent duplicate persistence
    _seen_tool_ids: set[str] = set()
    # Track messages sent but not yet completed (for drain)
    _pending_message_ids: set[str] = set()
    # Session tracking - defer session creation until first message
    # so the client can provide an existing session_id
    _session: dict[str, str | None] = {"id": None}

    async def _save_chat_message(
        msg_agent_id: str, message_id: str, role: str,
        content: str = "", tool_calls: list | None = None, meta: dict | None = None,
    ):
        """Persist a chat message to the database."""
        session_id = _session["id"]
        if not session_id:
            return  # Don't save without a valid session
        try:
            async with async_session_factory() as db:
                db.add(ChatMessage(
                    agent_id=msg_agent_id,
                    session_id=session_id,
                    message_id=message_id,
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    meta=meta,
                ))
                await db.commit()
        except Exception:
            pass  # Don't break chat if DB write fails

    _ws_connected = True

    def _process_event(raw_data: str):
        """Process a PubSub event for persistence tracking. Returns tuple on done/error, None otherwise."""
        nonlocal _streaming_responses, _seen_tool_ids, _pending_message_ids
        try:
            event = json.loads(raw_data)
            mid = event.get("message_id", "")
            etype = event.get("type", "")
            edata = event.get("data", {})

            if etype == "text":
                if mid not in _streaming_responses:
                    _streaming_responses[mid] = {"content": "", "tool_calls": []}
                _streaming_responses[mid]["content"] += str(edata.get("text", ""))
            elif etype == "tool_call":
                tool_use_id = str(edata.get("tool_use_id", ""))
                if tool_use_id not in _seen_tool_ids:
                    _seen_tool_ids.add(tool_use_id)
                    if mid not in _streaming_responses:
                        _streaming_responses[mid] = {"content": "", "tool_calls": []}
                    _streaming_responses[mid]["tool_calls"].append({
                        "tool": str(edata.get("tool", "")),
                        "input": json.dumps(edata.get("input", {})),
                    })
            elif etype == "error":
                _pending_message_ids.discard(mid)
                return ("error", mid, str(edata.get("message", "Unknown error")), None)
            elif etype == "done":
                _pending_message_ids.discard(mid)
                resp = _streaming_responses.pop(mid, {})
                meta = {
                    "cost_usd": edata.get("cost_usd"),
                    "duration_ms": edata.get("duration_ms"),
                    "num_turns": edata.get("num_turns"),
                }
                return ("done", mid, resp, meta)
        except (json.JSONDecodeError, Exception):
            pass
        return None

    async def forward_responses():
        """Forward Redis PubSub chat responses to the WebSocket client."""
        nonlocal _ws_connected
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")

                    # Forward to WebSocket if still connected
                    if _ws_connected:
                        try:
                            await websocket.send_text(data)
                        except Exception:
                            _ws_connected = False

                    # Track for persistence
                    result = _process_event(data)
                    if result:
                        if result[0] == "error":
                            await _save_chat_message(agent_id, result[1], "error", content=result[2])
                        elif result[0] == "done":
                            resp = result[2]
                            await _save_chat_message(
                                agent_id, result[1], "assistant",
                                content=resp.get("content", ""),
                                tool_calls=resp.get("tool_calls") or None,
                                meta=result[3],
                            )

                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _drain_remaining():
        """After WS disconnect, keep listening to persist pending responses.

        Uses _pending_message_ids (not just _streaming_responses) to determine
        if there are still unfinished messages, so we also catch cases where
        the agent hasn't started streaming yet when the user disconnects.
        """
        if not _session["id"]:
            return  # Don't persist without a valid session
        has_pending = bool(_pending_message_ids) or bool(_streaming_responses)
        if not has_pending:
            return  # Nothing to wait for
        deadline = asyncio.get_event_loop().time() + 120  # Wait up to 2 min
        try:
            while (_pending_message_ids or _streaming_responses) and asyncio.get_event_loop().time() < deadline:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    result = _process_event(data)
                    if result:
                        if result[0] == "error":
                            await _save_chat_message(agent_id, result[1], "error", content=result[2])
                        elif result[0] == "done":
                            resp = result[2]
                            content = resp.get("content", "")
                            tool_calls = resp.get("tool_calls") or None
                            if content or tool_calls:  # Only save non-empty
                                await _save_chat_message(
                                    agent_id, result[1], "assistant",
                                    content=content,
                                    tool_calls=tool_calls,
                                    meta=result[3],
                                )
                await asyncio.sleep(0.05)
        except Exception:
            pass
        # Save any remaining partial responses that never got a "done" event
        for mid, resp in _streaming_responses.items():
            content = resp.get("content", "").strip()
            tool_calls = resp.get("tool_calls") or None
            if content or tool_calls:  # Only save non-empty partials
                await _save_chat_message(
                    agent_id, mid, "assistant",
                    content=content,
                    tool_calls=tool_calls,
                    meta={"partial": True},
                )

    # Start forwarding responses in background
    forward_task = asyncio.create_task(forward_responses())

    try:
        while True:
            # Receive user messages from WebSocket
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"text": raw}

            # Handle stop/cancel action
            action = msg.get("action", "")
            if action == "stop":
                cancel_channel = f"agent:{agent_id}:chat:cancel"
                await _redis.client.publish(cancel_channel, "stop")
                await websocket.send_text(json.dumps({
                    "type": "cancelled",
                    "data": {"message": "Stop signal sent to agent"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                continue

            text = msg.get("text", "").strip()
            if not text:
                continue

            # --- AgentGuard: Rate limiting ---
            if not chat_rate_limiter.check(agent_id):
                await websocket.send_text(json.dumps({
                    "type": "security_block",
                    "data": {"reason": "Rate limit exceeded. Please wait before sending more messages."},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                continue

            # --- AgentGuard: Security check on incoming messages ---
            verdict = check_chat_message(text, source="user")
            if not verdict.allowed:
                await websocket.send_text(json.dumps({
                    "type": "security_block",
                    "data": {"reason": verdict.reason},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                if _redis and _redis.client:
                    await notify_security_block(
                        _redis.client, source="chat", reason=verdict.reason, agent_id=agent_id
                    )
                continue

            # Handle session switching from client
            if "session_id" in msg and msg["session_id"]:
                _session["id"] = msg["session_id"]

            # On /reset, start a new session
            if text == "/reset":
                _session["id"] = uuid.uuid4().hex[:12]
                # Notify client of new session ID
                await websocket.send_text(json.dumps({
                    "type": "session",
                    "data": {"session_id": _session["id"]},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                continue

            # If no session_id yet (first message without client-provided session),
            # generate one and notify client
            is_new_session = False
            if not _session["id"]:
                _session["id"] = uuid.uuid4().hex[:12]
                is_new_session = True

            # Generate message ID and push to agent's chat queue
            message_id = uuid.uuid4().hex[:12]
            _pending_message_ids.add(message_id)
            chat_payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": msg.get("model"),
            })

            # Save user message to DB
            await _save_chat_message(agent_id, message_id, "user", content=text)

            # Only send session event when a NEW session was created
            # (not on every message - that caused duplicate chat tabs)
            if is_new_session:
                await websocket.send_text(json.dumps({
                    "type": "session",
                    "data": {"session_id": _session["id"]},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))

            await _redis.client.lpush(f"agent:{agent_id}:chat", chat_payload)

            # Check if agent is currently busy — if so, notify client the message is queued
            try:
                agent_status = await _redis.client.hgetall(f"agent:{agent_id}:status")
                state = str(agent_status.get("state", ""))
                queue_depth = await _redis.client.llen(f"agent:{agent_id}:chat")
                if state == "working" or queue_depth > 1:
                    await websocket.send_text(json.dumps({
                        "agent_id": agent_id,
                        "message_id": message_id,
                        "type": "queued",
                        "data": {
                            "message": "Agent is busy — your message will be processed next.",
                            "queue_position": int(queue_depth),
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
            except Exception:
                pass  # Don't break chat if queue-depth check fails

            # Publish chat activity event to activity log
            preview = text[:60] + ("..." if len(text) > 60 else "")
            activity_event = json.dumps({
                "agent_id": agent_id,
                "task_id": "",
                "type": "system",
                "data": {"message": f"Chat message received: \"{preview}\""},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await _redis.client.publish(f"agent:{agent_id}:logs", activity_event)
            history_key = f"agent:{agent_id}:activity"
            await _redis.client.rpush(history_key, activity_event)
            await _redis.client.ltrim(history_key, -200, -1)

    except WebSocketDisconnect:
        _ws_connected = False
    except Exception:
        _ws_connected = False
    finally:
        forward_task.cancel()
        try:
            await forward_task
        except asyncio.CancelledError:
            pass
        # Drain remaining events so agent responses are always persisted
        await _drain_remaining()
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.websocket("/notifications")
async def ws_notifications(websocket: WebSocket, token: str | None = Query(None), ticket: str | None = Query(None)):
    """WebSocket for live notification push to the frontend. Requires ?ticket=<ticket> or ?token=<jwt> (legacy)."""
    if not _redis or not _redis.client:
        await websocket.close(code=1011, reason="Redis not initialized")
        return

    if not await _authenticate_ws(websocket, token=token, ticket=ticket):
        return

    await websocket.accept()
    pubsub = await _redis.subscribe("notifications:live")

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.5
            )
            if message and message["type"] == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                await websocket.send_text(data)
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await pubsub.unsubscribe("notifications:live")
        await pubsub.aclose()


@router.websocket("/logs")
async def ws_all_logs(websocket: WebSocket, token: str | None = Query(None), ticket: str | None = Query(None)):
    if not stream_manager:
        await websocket.close(code=1011, reason="Stream manager not initialized")
        return

    if not await _authenticate_ws(websocket, token=token, ticket=ticket):
        return

    try:
        await stream_manager.stream_all_logs(websocket)
    except WebSocketDisconnect:
        pass
