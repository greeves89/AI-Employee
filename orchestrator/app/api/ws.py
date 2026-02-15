import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.stream_manager import StreamManager
from app.db.session import async_session_factory
from app.models.chat_message import ChatMessage
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

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


@router.websocket("/agents/{agent_id}/logs")
async def ws_agent_logs(websocket: WebSocket, agent_id: str):
    if not stream_manager:
        await websocket.close(code=1011, reason="Stream manager not initialized")
        return

    try:
        await stream_manager.stream_agent_logs(websocket, agent_id)
    except WebSocketDisconnect:
        pass


@router.websocket("/agents/{agent_id}/chat")
async def ws_agent_chat(websocket: WebSocket, agent_id: str):
    """Bidirectional WebSocket for chatting with an agent.

    Client sends: {"text": "Hello"} or {"text": "/reset"}
    Server sends: {"type": "text", "data": {"text": "..."}, ...} (streamed)
    Server sends: {"type": "done", "data": {...}} when response complete
    """
    if not _redis or not _redis.client:
        await websocket.close(code=1011, reason="Redis not initialized")
        return

    # Check if agent container is alive before accepting
    if _docker:
        from app.db.session import async_session_factory
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
    # Session tracking - defer session creation until first message
    # so the client can provide an existing session_id
    _session: dict[str, str | None] = {"id": None}

    async def _save_chat_message(
        msg_agent_id: str, message_id: str, role: str,
        content: str = "", tool_calls: list | None = None, meta: dict | None = None,
    ):
        """Persist a chat message to the database."""
        try:
            async with async_session_factory() as db:
                db.add(ChatMessage(
                    agent_id=msg_agent_id,
                    session_id=_session["id"],
                    message_id=message_id,
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    meta=meta,
                ))
                await db.commit()
        except Exception:
            pass  # Don't break chat if DB write fails

    async def forward_responses():
        """Forward Redis PubSub chat responses to the WebSocket client."""
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

                    # Track responses for persistence
                    try:
                        event = json.loads(data)
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
                                    "input": json.dumps(edata.get("input", {}))[:200],
                                })
                        elif etype == "error":
                            await _save_chat_message(
                                agent_id, mid, "error",
                                content=str(edata.get("message", "Unknown error")),
                            )
                        elif etype == "done":
                            resp = _streaming_responses.pop(mid, {})
                            meta = {
                                "cost_usd": edata.get("cost_usd"),
                                "duration_ms": edata.get("duration_ms"),
                                "num_turns": edata.get("num_turns"),
                            }
                            await _save_chat_message(
                                agent_id, mid, "assistant",
                                content=resp.get("content", ""),
                                tool_calls=resp.get("tool_calls") or None,
                                meta=meta,
                            )
                    except (json.JSONDecodeError, Exception):
                        pass

                await asyncio.sleep(0.01)
        except Exception:
            pass

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

            text = msg.get("text", "").strip()
            if not text:
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

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        forward_task.cancel()
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.websocket("/notifications")
async def ws_notifications(websocket: WebSocket):
    """WebSocket for live notification push to the frontend."""
    if not _redis or not _redis.client:
        await websocket.close(code=1011, reason="Redis not initialized")
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
async def ws_all_logs(websocket: WebSocket):
    if not stream_manager:
        await websocket.close(code=1011, reason="Stream manager not initialized")
        return

    try:
        await stream_manager.stream_all_logs(websocket)
    except WebSocketDisconnect:
        pass
