import asyncio
import json
import logging
import mimetypes
import os
import re
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
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websocket"])

# Global stream manager and redis - initialized in main.py lifespan
stream_manager: StreamManager | None = None
_redis: RedisService | None = None
_docker: DockerService | None = None

_CHAT_ATTACHMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".md",
    ".zip", ".json", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp",
    ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".flac",
}
_active_chat_websockets: dict[str, WebSocket] = {}


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
            websocket.state.user_id = str(user_id)
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
            websocket.state.user_id = str(user.id)
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

    agent_container_id: str | None = None

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
                agent_container_id = agent.container_id
                status = _docker.get_container_status(agent.container_id)
                if status not in ("running", "created"):
                    await websocket.close(code=4010, reason=f"Agent container is {status}")
                    return
        except Exception:
            pass  # Allow connection attempt if check fails

    await websocket.accept()
    connection_key = f"{getattr(websocket.state, 'user_id', 'anonymous')}:{agent_id}:chat"
    previous = _active_chat_websockets.get(connection_key)
    if previous and previous is not websocket:
        try:
            await previous.close(code=4000, reason="Replaced by a newer chat connection")
        except Exception:
            pass
    _active_chat_websockets[connection_key] = websocket
    await websocket.send_text(json.dumps({
        "type": "ready",
        "data": {"agent_id": agent_id},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    # Subscribe to agent's chat response channel
    channel = f"agent:{agent_id}:chat:response"
    pubsub = await _redis.subscribe(channel)

    # Track streaming assistant responses for persistence
    _streaming_responses: dict[str, dict] = {}
    # Track seen tool_use_ids to prevent duplicate persistence
    _seen_tool_ids: set[str] = set()
    _forwarded_file_keys: set[tuple[str, str]] = set()
    # Track messages sent but not yet completed (for drain)
    _pending_message_ids: set[str] = set()
    # Session tracking - defer session creation until first message
    # so the client can provide an existing session_id
    _session: dict[str, str | None] = {"id": None}

    async def _save_chat_message(
        msg_agent_id: str, message_id: str, role: str,
        content: str = "", tool_calls: list | None = None, meta: dict | None = None,
        cost_usd: float | None = None,
        input_tokens: int | None = None, output_tokens: int | None = None,
    ):
        """Persist a chat message to the database."""
        session_id = _session["id"]
        if not session_id:
            return  # Don't save without a valid session
        try:
            async with async_session_factory() as db:
                existing = await db.scalar(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == msg_agent_id)
                    .where(ChatMessage.session_id == session_id)
                    .where(ChatMessage.message_id == message_id)
                    .where(ChatMessage.role == role)
                    .order_by(ChatMessage.id.asc())
                    .limit(1)
                )
                if existing:
                    existing.content = content or existing.content
                    existing.tool_calls = tool_calls or existing.tool_calls
                    merged_meta = dict(existing.meta or {})
                    for key, value in (meta or {}).items():
                        if value is not None:
                            if key == "presented_files":
                                existing_files = merged_meta.get("presented_files") or []
                                new_files = value or []
                                if not isinstance(existing_files, list):
                                    existing_files = []
                                if not isinstance(new_files, list):
                                    new_files = []
                                seen_paths = {
                                    str(item.get("path", ""))
                                    for item in existing_files
                                    if isinstance(item, dict)
                                }
                                merged_files = list(existing_files)
                                for item in new_files:
                                    if not isinstance(item, dict):
                                        continue
                                    path = str(item.get("path", ""))
                                    if path and path not in seen_paths:
                                        seen_paths.add(path)
                                        merged_files.append(item)
                                merged_meta[key] = merged_files
                            else:
                                merged_meta[key] = value
                    existing.meta = merged_meta or None
                    existing.cost_usd = cost_usd if cost_usd is not None else existing.cost_usd
                    existing.input_tokens = input_tokens if input_tokens is not None else existing.input_tokens
                    existing.output_tokens = output_tokens if output_tokens is not None else existing.output_tokens
                else:
                    db.add(ChatMessage(
                        agent_id=msg_agent_id,
                        session_id=session_id,
                        message_id=message_id,
                        role=role,
                        content=content,
                        tool_calls=tool_calls,
                        meta=meta,
                        cost_usd=cost_usd,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    ))
                await db.commit()
        except Exception:
            pass  # Don't break chat if DB write fails

    _ws_connected = True

    def _file_attachment_payload(path: str, caption: str = "Download") -> dict | None:
        if not path or not _docker or not agent_container_id:
            return None
        safe_path = os.path.normpath(path.rstrip(".,;:"))
        if safe_path == "/workspace" or not safe_path.startswith("/workspace/"):
            return None
        _, ext = os.path.splitext(safe_path.lower())
        if ext not in _CHAT_ATTACHMENT_EXTENSIONS:
            return None
        try:
            code, output = _docker.exec_in_container(
                agent_container_id,
                ["stat", "-c", "%s|%F", safe_path],
            )
            if code != 0:
                return None
            size_raw, kind = (output.strip().split("|", 1) + [""])[:2]
            size = int(size_raw)
            if "regular file" not in kind or size <= 0 or size > 50 * 1024 * 1024:
                return None
            return {
                "path": safe_path,
                "filename": os.path.basename(safe_path),
                "media_type": mimetypes.guess_type(safe_path)[0] or "application/octet-stream",
                "size": size,
                "caption": caption or "Download",
            }
        except Exception:
            logger.debug("auto-present file detection failed for %s", safe_path, exc_info=True)
            return None

    def _auto_presented_files_from_text(content: str) -> list[dict]:
        """Best-effort attachment fallback for agents that mention files but forget present_file."""
        if not content:
            return []
        found: list[dict] = []
        seen: set[str] = set()
        for match in re.finditer(r"/workspace/[^\s`'\"<>)\]}]+", content):
            payload = _file_attachment_payload(match.group(0), "Download")
            if payload and payload["path"] not in seen:
                seen.add(payload["path"])
                found.append(payload)
        return found

    def _auto_presented_files_from_tool_calls(tool_calls: list[dict] | None) -> list[dict]:
        """Attachment fallback when present_file tool results are not emitted by the CLI stream."""
        found: list[dict] = []
        seen: set[str] = set()
        for call in tool_calls or []:
            tool = str(call.get("tool", ""))
            if "present_file" not in tool:
                continue
            raw_input = call.get("input") or "{}"
            try:
                payload_in = json.loads(raw_input) if isinstance(raw_input, str) else dict(raw_input)
            except Exception:
                continue
            payload = _file_attachment_payload(
                str(payload_in.get("path") or ""),
                str(payload_in.get("caption") or "Download"),
            )
            if payload and payload["path"] not in seen:
                seen.add(payload["path"])
                found.append(payload)
        return found

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
            elif etype == "image":
                if mid not in _streaming_responses:
                    _streaming_responses[mid] = {"content": "", "tool_calls": []}
                data_str = str(edata.get("data", ""))
                if data_str:
                    _streaming_responses[mid].setdefault("images", []).append({
                        "media_type": str(edata.get("media_type", "image/png")),
                        "data": data_str,
                    })
            elif etype == "file":
                if mid not in _streaming_responses:
                    _streaming_responses[mid] = {"content": "", "tool_calls": []}
                if edata.get("path"):
                    _streaming_responses[mid].setdefault("files", []).append({
                        "path": str(edata.get("path", "")),
                        "filename": str(edata.get("filename", "")),
                        "media_type": str(edata.get("media_type", "application/octet-stream")),
                        "size": int(edata.get("size") or 0),
                        "caption": str(edata.get("caption", "")),
                    })
            elif etype == "error":
                _pending_message_ids.discard(mid)
                return ("error", mid, str(edata.get("message", "Unknown error")), None)
            elif etype == "done":
                _pending_message_ids.discard(mid)
                resp = _streaming_responses.pop(mid, {})
                final_text = (
                    edata.get("text")
                    or edata.get("content")
                    or edata.get("result")
                    or ""
                )
                if final_text and not resp.get("content"):
                    resp["content"] = str(final_text)
                auto_files = _auto_presented_files_from_text(str(resp.get("content", "")))
                auto_files.extend(_auto_presented_files_from_tool_calls(resp.get("tool_calls")))
                if auto_files:
                    existing = {f.get("path") for f in resp.get("files", []) if isinstance(f, dict)}
                    resp.setdefault("files", []).extend(
                        f for f in auto_files if f.get("path") not in existing
                    )
                meta = {
                    "cost_usd": edata.get("cost_usd"),
                    "duration_ms": edata.get("duration_ms"),
                    "num_turns": edata.get("num_turns"),
                    "input_tokens": edata.get("input_tokens"),
                    "output_tokens": edata.get("output_tokens"),
                }
                if resp.get("images"):
                    meta["presented_images"] = resp["images"]
                if resp.get("files"):
                    meta["presented_files"] = resp["files"]
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
                    try:
                        _event_for_forward = json.loads(data)
                        if _event_for_forward.get("type") == "file":
                            _forwarded_file_keys.add((
                                str(_event_for_forward.get("message_id", "")),
                                str((_event_for_forward.get("data") or {}).get("path", "")),
                            ))
                    except Exception:
                        pass

                    # Track for persistence
                    result = _process_event(data)
                    try:
                        _event_for_persist = json.loads(data)
                    except Exception:
                        _event_for_persist = {}
                    if _event_for_persist.get("type") == "file":
                        mid = str(_event_for_persist.get("message_id", ""))
                        file_payload = _event_for_persist.get("data") or {}
                        if mid and isinstance(file_payload, dict) and file_payload.get("path"):
                            await _save_chat_message(
                                agent_id, mid, "assistant",
                                content=str(file_payload.get("caption") or ""),
                                meta={"presented_files": [file_payload]},
                            )
                    if result and result[0] == "done":
                        meta = result[3] or {}
                        for file_payload in meta.get("presented_files") or []:
                            file_key = (str(result[1]), str(file_payload.get("path", "")))
                            if file_key in _forwarded_file_keys:
                                continue
                            _forwarded_file_keys.add(file_key)
                            if not _ws_connected:
                                break
                            try:
                                await websocket.send_text(json.dumps({
                                    "type": "file",
                                    "message_id": result[1],
                                    "data": file_payload,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }))
                            except Exception:
                                _ws_connected = False

                    # Forward to WebSocket if still connected
                    if _ws_connected:
                        try:
                            await websocket.send_text(data)
                        except Exception:
                            _ws_connected = False

                    if result:
                        if result[0] == "error":
                            await _save_chat_message(agent_id, result[1], "error", content=result[2])
                        elif result[0] == "done":
                            resp = result[2]
                            meta = result[3] or {}
                            await _save_chat_message(
                                agent_id, result[1], "assistant",
                                content=resp.get("content", ""),
                                tool_calls=resp.get("tool_calls") or None,
                                meta=meta,
                                cost_usd=meta.get("cost_usd"),
                                input_tokens=meta.get("input_tokens"),
                                output_tokens=meta.get("output_tokens"),
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
                                meta = result[3] or {}
                                await _save_chat_message(
                                    agent_id, result[1], "assistant",
                                    content=content,
                                    tool_calls=tool_calls,
                                    meta=meta,
                                    cost_usd=meta.get("cost_usd"),
                                    input_tokens=meta.get("input_tokens"),
                                    output_tokens=meta.get("output_tokens"),
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

            # Pasted/attached images: list of {media_type, data(base64)}.
            # Keep only supported types within the 5 MB / 4-image budget.
            images: list[dict] = []
            _allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
            for img in (msg.get("images") or [])[:4]:
                if not isinstance(img, dict):
                    continue
                data = img.get("data")
                mt = img.get("media_type", "image/jpeg")
                if data and mt in _allowed_types and (len(data) * 3 // 4) <= 5 * 1024 * 1024:
                    images.append({"media_type": mt, "data": data})

            if not text and not images:
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
            source = str(msg.get("source") or "webapp")
            chat_payload = json.dumps({
                "id": message_id,
                "text": text,
                "model": msg.get("model"),
                "images": images,
                "source": source,
                "chat_session_id": _session["id"],
            })

            # Save user message to DB
            db_content = text or (f"[{len(images)} Bild(er) angehängt]" if images else "")
            await _save_chat_message(
                agent_id, message_id, "user",
                content=db_content,
                meta={"source": source},
            )

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
                            "message": "Agent is working — your message was added to the current turn.",
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
        if _active_chat_websockets.get(connection_key) is websocket:
            _active_chat_websockets.pop(connection_key, None)
        forward_task.cancel()
        try:
            await forward_task
        except asyncio.CancelledError:
            pass
        # Drain remaining events so agent responses are always persisted
        await _drain_remaining()
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.websocket("/agents/{agent_id}/voice")
async def ws_agent_voice(
    websocket: WebSocket, agent_id: str,
    token: str | None = Query(None), ticket: str | None = Query(None),
):
    """Live voice session with an agent.

    Client → server:
      {"type":"audio_chunk","data":{"b64":"..."}}    — append audio to buffer
      {"type":"commit","data":{"language":"de"}}     — end of utterance
      {"type":"interrupt"}                            — barge-in, cancel TTS
      {"type":"ping"}                                 — keepalive

    Server → client:
      {"type":"transcript","data":{"text":"..."}}    — STT result
      {"type":"response","data":{"text":"..."}}      — final container response
      {"type":"tts_start","data":{"tag":"ack|main"}}
      {"type":"audio_chunk","data":{"tag":..,"mime":"audio/mpeg","b64":"..."}}
      {"type":"tts_end","data":{"tag":...}}
      {"type":"done","data":{}}                       — turn complete
      {"type":"error","data":{"message":"..."}}
    """
    import base64
    from app.services.voice_session import VoiceSession

    if not _redis or not _redis.client:
        await websocket.close(code=1011, reason="Redis not initialized")
        return

    if not await _authenticate_ws(websocket, token=token, ticket=ticket):
        return

    # Resolve user id from the ticket / token. We need it for the session.
    # (Ticket was consumed in _authenticate_ws; re-fetch user separately.)
    user_id = "unknown"
    try:
        async with async_session_factory() as db:
            user = await get_current_user_ws(token, db) if token else None
            if user:
                user_id = str(user.id)
    except Exception:
        pass

    # Verify agent container is alive
    if _docker:
        from app.models.agent import Agent
        from sqlalchemy import select
        try:
            async with async_session_factory() as db:
                agent = (await db.execute(
                    select(Agent).where(Agent.id == agent_id)
                )).scalar_one_or_none()
                if not agent or not agent.container_id:
                    await websocket.close(code=4004, reason="Agent not available")
                    return
                status = _docker.get_container_status(agent.container_id)
                if status not in ("running", "created"):
                    await websocket.close(code=4010, reason=f"Agent is {status}")
                    return
        except Exception:
            pass

    await websocket.accept()

    session = VoiceSession(agent_id=agent_id, user_id=user_id, redis=_redis)
    async with async_session_factory() as db:
        await session.init(db)

    async def pump_outbound():
        try:
            async for evt in session.outbound():
                await websocket.send_text(json.dumps(evt))
        except Exception:
            logger.warning("voice outbound pump stopped agent=%s", agent_id, exc_info=True)

    def _log_voice_turn_done(task: asyncio.Task) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            logger.warning("voice turn cancelled agent=%s session=%s", agent_id, session.session_id)
            return
        if exc:
            logger.error(
                "voice turn crashed agent=%s session=%s",
                agent_id,
                session.session_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    out_task = asyncio.create_task(pump_outbound())

    try:
        await websocket.send_text(json.dumps({
            "type": "ready",
            "data": {"session_id": session.session_id},
        }))
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            mdata = msg.get("data") or {}

            if mtype == "audio_chunk":
                b64 = mdata.get("b64", "")
                if b64:
                    try:
                        chunk = base64.b64decode(b64)
                        session.push_audio_chunk(chunk)
                        logger.warning(
                            "voice ws chunk agent=%s session=%s bytes=%d",
                            agent_id,
                            session.session_id,
                            len(chunk),
                        )
                    except Exception:
                        logger.warning("voice ws invalid audio chunk agent=%s", agent_id, exc_info=True)
            elif mtype == "commit":
                lang = mdata.get("language")
                # Process turn in background so we keep accepting interrupts
                logger.warning(
                    "voice ws commit agent=%s session=%s language=%s",
                    agent_id,
                    session.session_id,
                    lang,
                )
                task = asyncio.create_task(session.commit_turn(language=lang))
                task.add_done_callback(_log_voice_turn_done)
            elif mtype == "interrupt":
                await session.interrupt()
            elif mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            # unknown types: ignore
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("voice ws error")
    finally:
        await session.close()
        out_task.cancel()
        try:
            await out_task
        except asyncio.CancelledError:
            pass


async def _notif_visible_agent_ids(user_id: str | None) -> set[str]:
    """Agent ids whose notifications a user may receive on the live stream
    (own + unowned + shared) — same scope as the REST notification endpoints,
    so the live push never leaks another user's agent notifications."""
    if not user_id:
        return set()
    from sqlalchemy import or_, select

    from app.models.agent import Agent
    from app.models.agent_access import AgentAccess

    async with async_session_factory() as db:
        owned = (await db.execute(
            select(Agent.id).where(or_(Agent.user_id == user_id, Agent.user_id.is_(None)))
        )).scalars().all()
        shared = (await db.execute(
            select(AgentAccess.agent_id).where(AgentAccess.user_id == user_id)
        )).scalars().all()
    return set(owned) | set(shared)


@router.websocket("/notifications")
async def ws_notifications(websocket: WebSocket, token: str | None = Query(None), ticket: str | None = Query(None)):
    """WebSocket for live notification push to the frontend. Requires ?ticket=<ticket> or ?token=<jwt> (legacy)."""
    if not _redis or not _redis.client:
        await websocket.close(code=1011, reason="Redis not initialized")
        return

    if not await _authenticate_ws(websocket, token=token, ticket=ticket):
        return

    await websocket.accept()
    user_id = getattr(websocket.state, "user_id", None)
    visible = await _notif_visible_agent_ids(user_id)
    pubsub = await _redis.subscribe("notifications:live")

    try:
        ticks = 0
        while True:
            ticks += 1
            if ticks % 60 == 0:  # refresh visibility (~30s) to pick up new agents/access
                visible = await _notif_visible_agent_ids(user_id)
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.5
            )
            if message and message["type"] == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                # Scope: only forward notifications for agents this user may see.
                try:
                    aid = (json.loads(data).get("data") or {}).get("agent_id")
                except Exception:
                    aid = None
                if aid not in visible:
                    continue
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
