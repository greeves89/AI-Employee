import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from app.core.task_router import UnknownAgentError

from app.api.router import api_router
from app.api.ws import init_stream_manager
from app.config import settings
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


# --- Security Headers Middleware ---


class SecurityHeadersMiddleware:
    """Pure-ASGI middleware that stamps security headers on every response.

    Implemented as pure ASGI (not BaseHTTPMiddleware) on purpose: BaseHTTPMiddleware
    runs the downstream app inside its own anyio task/cancel-scope. When a client
    disconnects mid-request, that inner task is cancelled while an endpoint still
    holds a checked-out SQLAlchemy connection, orphaning it (the pool then logs
    "non-checked-in connection ... will be terminated"). Pure ASGI keeps the request
    on the original task, so cancellation unwinds the session cleanly.
    """

    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' ws: wss:; "
        "font-src 'self' data:; "
        "frame-ancestors 'none'"
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["X-XSS-Protection"] = "1; mode=block"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["Content-Security-Policy"] = self._CSP
            await send(message)

        await self.app(scope, receive, send_with_headers)


# --- API Rate Limiting Middleware ---


class APIRateLimitMiddleware:
    """Per-user / per-IP rate limiting backed by Redis.

    Uses Redis INCR + EXPIRE for distributed, restart-safe counters.
    Falls back to in-memory tracking if Redis is unavailable.

    Implemented as pure ASGI (not BaseHTTPMiddleware) so client disconnects don't
    orphan checked-out DB connections — see SecurityHeadersMiddleware for the why.
    """

    def __init__(self, app, max_requests: int = 120, window_seconds: int = 60):
        self.app = app
        self.max_requests = max_requests
        self.window = window_seconds
        # In-memory fallback (only used if Redis is unreachable)
        self._fallback: dict[str, list[float]] = {}
        # Throttle "rate limit exceeded" WARNINGs to once per key+window in the
        # fallback path, so a client hammering the endpoint can't flood the log.
        self._fallback_logged: dict[str, float] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)

        # Skip rate limiting for health checks and WebSocket upgrades
        path = request.url.path
        if path in ("/health", "/healthz") or request.headers.get("upgrade", "").lower() == "websocket":
            await self.app(scope, receive, send)
            return

        # Identify caller: user_id from JWT cookie, fallback to IP
        key = request.client.host if request.client else "unknown"
        access_token = request.cookies.get("access_token")
        if access_token:
            try:
                from app.core.auth import decode_token
                payload = decode_token(access_token)
                key = f"user:{payload.get('sub', key)}"
            except Exception:
                pass  # Use IP if token is invalid

        redis_key = f"ratelimit:{key}"

        # Try Redis-backed rate limiting (distributed, survives restarts)
        redis_svc = getattr(request.app.state, "redis", None)
        redis_client = getattr(redis_svc, "client", None) if redis_svc else None

        if redis_client:
            # Only Redis I/O is guarded here. The downstream app call and the 429
            # response are issued *outside* the try so a downstream 500 propagates
            # instead of being swallowed and re-dispatched (double app-call).
            redis_ok = False
            retry_after: int | None = None  # None => allowed, int => blocked
            try:
                current = await redis_client.incr(redis_key)
                if current == 1:
                    await redis_client.expire(redis_key, self.window)
                if current > self.max_requests:
                    ttl = await redis_client.ttl(redis_key)
                    retry_after = max(ttl, 1)
                    # Log once per key+window (#521): INCR is monotonic within the
                    # window, so exactly one request hits current == max+1. Logging
                    # only on that first over-limit request avoids the observed
                    # flood (126 identical WARNINGs for one user in one window).
                    if current == self.max_requests + 1:
                        logger.warning(f"Rate limit exceeded for {key} ({current}/{self.max_requests})")
                redis_ok = True
            except Exception:
                pass  # Redis unavailable — fall through to in-memory

            if redis_ok:
                if retry_after is not None:
                    response = Response(
                        content='{"detail":"Rate limit exceeded. Try again later."}',
                        status_code=429,
                        media_type="application/json",
                        headers={"Retry-After": str(retry_after)},
                    )
                    await response(scope, receive, send)
                else:
                    await self.app(scope, receive, send)
                return

        # In-memory fallback
        now = time.time()
        if key not in self._fallback:
            self._fallback[key] = []
        self._fallback[key] = [t for t in self._fallback[key] if now - t < self.window]

        if len(self._fallback[key]) >= self.max_requests:
            # Same log-once-per-window throttle as the Redis path (#521).
            last_logged = self._fallback_logged.get(key, 0.0)
            if now - last_logged >= self.window:
                self._fallback_logged[key] = now
                logger.warning(f"Rate limit exceeded for {key} (in-memory fallback)")
            response = Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(self.window)},
            )
            await response(scope, receive, send)
            return

        self._fallback[key].append(now)
        await self.app(scope, receive, send)


# --- Config Validation ---


def _validate_config() -> None:
    """Validate critical security settings at startup."""
    warnings = []

    if not settings.encryption_key:
        warnings.append("ENCRYPTION_KEY is not set - OAuth token encryption will fail!")

    if settings.api_secret_key == "change-me-in-production":
        raise RuntimeError(
            "FATAL: API_SECRET_KEY is still the default value 'change-me-in-production'. "
            "Set a strong random key (min 32 chars) in your .env file. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    if not settings.anthropic_api_key and not settings.claude_code_oauth_token:
        warnings.append("Neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set.")

    for w in warnings:
        logger.warning(f"CONFIG WARNING: {w}")


# --- Background Tasks ---


async def _refresh_oauth_tokens(redis: RedisService) -> None:
    """Background task that refreshes third-party OAuth tokens before they expire."""
    while True:
        try:
            from app.db.session import async_session_factory
            from app.services.oauth_service import OAuthService

            async with async_session_factory() as db:
                service = OAuthService(db, redis)
                await service.refresh_expiring_tokens()
        except Exception:
            pass
        await asyncio.sleep(300)  # Check every 5 minutes


async def _refresh_mcp_oauth_tokens() -> None:
    """Keep OAuth-protected MCP server tokens fresh on a timer (#488).

    ``refresh_if_needed`` previously ran only while building a new agent container,
    so a stored MCP access token expired within ~1h and every agent lost the server
    until recreated. This periodic sweep keeps the persisted token valid; the
    per-server advisory lock (#462) makes it safe to run alongside agent startup.
    """
    while True:
        try:
            from app.db.session import async_session_factory
            from app.services.mcp_oauth_refresh import refresh_all_oauth_servers

            async with async_session_factory() as db:
                await refresh_all_oauth_servers(db)
        except Exception:
            logger.exception("MCP OAuth periodic sweep failed; will retry in 5 min")
        await asyncio.sleep(300)  # Check every 5 minutes


async def _refresh_claude_token() -> None:
    """Background task that manages the Claude OAuth token lifecycle.

    - Reads token from host-auth/token.json (synced from macOS Keychain by launchd)
    - Only refreshes via Anthropic OAuth when token is actually expired
    - Checks file for changes every 2 min, but only calls Anthropic when needed
    - FORCED refresh daily at 01:00 UTC (03:00 German time) to prevent auth failures
    """
    from app.services.claude_token_service import ClaudeTokenService

    service = ClaudeTokenService()

    # Load initial token (DB → Keychain file → env, same order as refresh)
    await service.write_initial_token()

    last_forced_refresh_date: str = ""

    while True:
        try:
            success = await service.refresh_access_token()
            if not success:
                logger.warning(
                    "No token file found at /host-auth/token.json — "
                    "ensure launchd sync job is running on host."
                )

            # Forced OAuth refresh at 01:00 UTC (= 03:00 German CEST / 02:00 CET)
            now = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")
            if now.hour == 1 and now.minute < 5 and today_str != last_forced_refresh_date:
                logger.info("[Token] Starting scheduled forced token refresh (03:00 DE)")
                last_forced_refresh_date = today_str
                try:
                    from app.db.session import async_session_factory
                    from app.services.oauth_service import OAuthService
                    from app.services.redis_service import RedisService as RS

                    redis = RS(settings.redis_url)
                    await redis.connect()
                    async with async_session_factory() as db:
                        oauth_svc = OAuthService(db, redis)
                        await oauth_svc.refresh_expiring_tokens()
                        # Also force-refresh Anthropic even if not "expiring"
                        try:
                            from app.models.oauth_integration import OAuthIntegration, OAuthProvider
                            from sqlalchemy import select as sel
                            result = await db.execute(
                                sel(OAuthIntegration).where(
                                    OAuthIntegration.provider == OAuthProvider.ANTHROPIC
                                )
                            )
                            integration = result.scalar_one_or_none()
                            if integration and integration.refresh_token_encrypted:
                                await oauth_svc._refresh_token(integration)
                                await db.commit()
                                # Re-sync to shared volume
                                await service.refresh_access_token()
                                logger.info("[Token] Forced Anthropic token refresh completed")
                        except Exception as e:
                            logger.warning(f"[Token] Forced Anthropic refresh failed: {e}")
                    await redis.disconnect()
                except Exception as e:
                    logger.error(f"[Token] Scheduled refresh error: {e}")

        except Exception as e:
            logger.error(f"Token sync task error: {e}")

        await asyncio.sleep(30)  # Check file every 30s (cheap file read, NOT an API call)


async def _listen_task_events(redis: RedisService) -> None:
    """Background task that listens for task start + completion events from agents."""
    try:
        pubsub = await redis.subscribe("task:completions")
        # Also subscribe to task:started channel
        if redis.client:
            await pubsub.subscribe("task:started")
        logger.info("[TaskListener] Started listening on task:completions + task:started")
        print("[TaskListener] Started listening on task:completions + task:started")
    except Exception as e:
        logger.error(f"[TaskListener] Failed to start: {e}", exc_info=True)
        return

    while True:
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                print(f"[TaskListener] Received event on {message.get('channel')}")
                channel = message.get("channel", b"")
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")

                data = message["data"]
                if isinstance(data, str):
                    data = json.loads(data)
                elif isinstance(data, bytes):
                    data = json.loads(data.decode("utf-8"))

                # Import here to avoid circular imports
                from app.core.load_balancer import LoadBalancer
                from app.core.task_router import TaskRouter
                from app.db.session import async_session_factory

                async with async_session_factory() as db:
                    lb = LoadBalancer(redis)
                    router = TaskRouter(db, redis, lb)
                    if channel == "task:started":
                        await router.handle_task_start(data)
                    else:
                        await router.handle_task_completion(data)
        except Exception as e:
            logger.error(f"[TaskListener] Error processing task event: {e}", exc_info=True)
            await asyncio.sleep(1)


async def _listen_chat_completions(redis: RedisService) -> None:
    """Background listener that persists chat responses independent of WebSocket connections.

    Ensures chat responses are saved to DB even if the user navigated away
    (WebSocket disconnected) before the agent finished responding.
    """
    pubsub = await redis.subscribe("chat:completions")

    while True:
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
                data = message["data"]
                if isinstance(data, str):
                    data = json.loads(data)
                elif isinstance(data, bytes):
                    data = json.loads(data.decode("utf-8"))

                agent_id = data.get("agent_id", "")
                message_id = data.get("message_id", "")
                event_data = data.get("data", {})
                source = data.get("source", "chat")

                if not agent_id or not message_id:
                    continue

                from app.db.session import async_session_factory
                from app.models.agent import Agent
                from app.models.chat_message import ChatMessage
                from app.models.notification import Notification
                from app.core.push import push_to_user
                from sqlalchemy import select as sel
                from sqlalchemy.exc import IntegrityError

                async with async_session_factory() as db:
                    # Frueher stand hier "Zeile da? -> ueberspringen". Genau das war
                    # der Fehler: beim Trennen der Verbindung schreibt der Browser
                    # einen ZWISCHENSTAND weg — Werkzeugaufrufe schon da, der Text
                    # noch nicht. Danach kam dieses ``done`` mit dem fertigen Text,
                    # fand die Zeile und liess sie stehen. Zurueck blieb ein Chat
                    # mit Werkzeugaufrufen und ohne Antwort. Jetzt wird ergaenzt
                    # (``upsert_chat_message``), und nur eine WIRKLICH neue Zeile
                    # loest eine Benachrichtigung aus.

                    # Look up session_id from the user message (normal chat flow).
                    user_msg = await db.scalar(
                        sel(ChatMessage).where(
                            ChatMessage.agent_id == agent_id,
                            ChatMessage.message_id == message_id,
                            ChatMessage.role == "user",
                        )
                    )
                    if user_msg:
                        session_id = user_msg.session_id
                    elif source == "scheduler":
                        # Scheduler-originated tasks have no user message. Store
                        # them in a stable session so app/web history can render
                        # files delivered via present_file after the fact.
                        session_id = "scheduler"
                    else:
                        print(f"[ChatPersist] No user message found for {message_id}, skipping")
                        continue

                    content = str(
                        event_data.get("text")
                        or event_data.get("content")
                        or event_data.get("result")
                        or ""
                    )
                    tool_calls = event_data.get("tool_calls")
                    meta = {
                        "cost_usd": event_data.get("cost_usd"),
                        "duration_ms": event_data.get("duration_ms"),
                        "num_turns": event_data.get("num_turns"),
                        "presented_files": event_data.get("presented_files"),
                        "source": source if source != "chat" else None,
                    }
                    meta = {k: v for k, v in meta.items() if v is not None}

                    from app.services.chat_persistence import upsert_chat_message
                    is_new = await upsert_chat_message(
                        agent_id, session_id, message_id, "assistant",
                        content=content, tool_calls=tool_calls, meta=meta,
                    )
                    if not is_new:
                        # Ergaenzt, und der Nutzer hatte den Text schon vor Augen
                        # (der Browser hatte die Zeile vollstaendig geschrieben).
                        # Nicht noch einmal benachrichtigen, nicht noch einmal
                        # einbetten. War die Zeile dagegen leer und bekommt hier
                        # ihren Text, meldet ``upsert_chat_message`` True — dann
                        # war der Nutzer weg und soll es erfahren.
                        continue
                    agent = await db.scalar(sel(Agent).where(Agent.id == agent_id))
                    title = agent.name if agent else "AI Employee"
                    body = _chat_notification_body(content, meta)
                    notif = Notification(
                        agent_id=agent_id,
                        type="info",
                        title=title,
                        message=body,
                        priority="normal",
                        action_url=f"/agents/{agent_id}",
                        meta={
                            "type": "chat_message",
                            "agent_id": agent_id,
                            "session_id": session_id,
                            "message_id": message_id,
                        },
                    )
                    db.add(notif)
                    try:
                        await db.commit()
                    except IntegrityError:
                        await db.rollback()
                        continue
                    await db.refresh(notif)
                    await redis.client.publish(
                        "notifications:live",
                        json.dumps({
                            "type": "notification",
                            "data": _notification_response(notif),
                        }),
                    )
                    if agent and agent.user_id:
                        await push_to_user(
                            db,
                            agent.user_id,
                            title,
                            body,
                            data=_notification_push_payload(notif),
                        )
                    print(
                        f"[ChatPersist] Saved response for {message_id} "
                        f"(agent={agent_id}, session={session_id}, source={source})"
                    )
                    # Auto-embed this exchange into long-term memory so it's recallable
                    # across channels (voice/agent search). Skip machine-originated turns.
                    if source not in ("scheduler",) and session_id != "scheduler":
                        try:
                            from app.services.conversation_memory import save_conversation_memory
                            await save_conversation_memory(
                                db, agent_id, session_id, source,
                                getattr(user_msg, "content", "") if user_msg else "",
                                content,
                            )
                        except Exception as _e:  # noqa: BLE001
                            print(f"[ConvMemory] hook error: {_e}")
        except Exception as e:
            print(f"[ChatPersist] Error: {e}")
            await asyncio.sleep(1)


def _chat_notification_body(content: str, meta: dict) -> str:
    files = meta.get("presented_files")
    if isinstance(files, list) and files:
        count = len(files)
        return "Neue Datei erhalten" if count == 1 else f"{count} neue Dateien erhalten"
    text = " ".join((content or "").split())
    if not text:
        return "Neue Nachricht erhalten"
    return text[:197] + "..." if len(text) > 200 else text


def _notification_response(notif) -> dict:
    return {
        "id": notif.id,
        "agent_id": notif.agent_id,
        "type": notif.type,
        "title": notif.title,
        "message": notif.message,
        "priority": notif.priority,
        "read": notif.read,
        "action_url": notif.action_url,
        "meta": notif.meta,
        "created_at": notif.created_at.isoformat() if notif.created_at else "",
    }


def _notification_push_payload(notif) -> dict:
    meta = notif.meta or {}
    payload = {
        "notification_id": str(notif.id),
        "agent_id": notif.agent_id,
        "type": notif.type,
        "action_url": notif.action_url or "",
        "meta": meta,
    }
    if isinstance(meta, dict):
        for key in ("task_id", "session_id", "message_id"):
            if meta.get(key):
                payload[key] = str(meta[key])
    return payload


async def _persist_task_steps(redis: RedisService) -> None:
    """Persist per-step task execution events for time-travel replay (issue #54).

    Subscribes to the global `agents:logs:all` channel and writes one TaskStep
    row per event. The per-task sequence counter is kept in memory and seeded
    from the DB on a miss so it survives an orchestrator restart mid-task.
    """
    from datetime import datetime as _dt

    from app.db.session import async_session_factory
    from app.models.task_step import TaskStep
    from sqlalchemy import func as _func, select as _sel

    pubsub = await redis.subscribe("agents:logs:all")
    seq_cache: dict[str, int] = {}
    logger.info("[StepPersist] Listening on agents:logs:all for task-step persistence")

    while True:
        try:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message or message["type"] != "message":
                await asyncio.sleep(0.01)
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if isinstance(data, str):
                data = json.loads(data)

            task_id = data.get("task_id", "")
            event_type = data.get("type", "")
            if not task_id or not event_type:
                continue

            async with async_session_factory() as db:
                if task_id not in seq_cache:
                    existing_max = await db.scalar(
                        _sel(_func.max(TaskStep.sequence)).where(TaskStep.task_id == task_id)
                    )
                    seq_cache[task_id] = (existing_max + 1) if existing_max is not None else 0
                seq = seq_cache[task_id]

                ts_raw = data.get("timestamp")
                try:
                    ts = _dt.fromisoformat(ts_raw) if ts_raw else _dt.now(timezone.utc)
                except (ValueError, TypeError):
                    ts = _dt.now(timezone.utc)

                db.add(TaskStep(
                    task_id=task_id,
                    sequence=seq,
                    event_type=event_type,
                    event_data=data.get("data", {}),
                    timestamp=ts,
                ))
                try:
                    await db.commit()
                    seq_cache[task_id] = seq + 1
                except Exception:
                    await db.rollback()  # FK miss (task not yet persisted) — skip

            # A terminal event ends the task — drop its counter to bound memory.
            if event_type in ("result", "error"):
                seq_cache.pop(task_id, None)
        except Exception as e:
            print(f"[StepPersist] Error: {e}")
            await asyncio.sleep(1)


async def _persist_agent_messages(redis: RedisService) -> None:
    """Listen for inter-agent message events and persist them to DB."""
    from app.db.session import async_session_factory
    from app.models.agent_message import AgentMessage

    pubsub = await redis.subscribe("agent:messages:persist")
    while True:
        try:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
            if message and message["type"] == "message":
                data = json.loads(message["data"])
                async with async_session_factory() as db:
                    db.add(AgentMessage(
                        message_id=data.get("id") or data.get("message_id"),
                        from_agent_id=data.get("from_agent_id", ""),
                        from_agent_name=data.get("from_name", ""),
                        to_agent_id=data.get("to_agent_id", ""),
                        text=data.get("text", ""),
                        message_type=data.get("message_type") or ("response" if data.get("is_reply") else "message"),
                        reply_to=data.get("reply_to"),
                    ))
                    await db.commit()
        except Exception as e:
            logger.debug(f"[MessagePersist] Error: {e}")
            await asyncio.sleep(1)


async def _init_db_from_models() -> None:
    """Create all tables from SQLAlchemy models and stamp Alembic to HEAD.

    Used as fallback when Alembic migrations fail (fresh DB, broken chain).
    """
    import subprocess

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text as _sql_text

    from app.models import Base  # noqa: F401

    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tables created from SQLAlchemy models")

    # pgvector must ALWAYS be present. The embedding columns are pgvector
    # `vector(1024)` added via raw-SQL migrations, NOT in the SQLAlchemy models —
    # so on a fresh DB (create_all + `alembic stamp head` below) they would be
    # skipped. Ensure the extension + columns + HNSW indexes here, idempotently,
    # on every startup so semantic search (brain/skill/memory) always works.
    # Kept in its own transaction so a missing extension can never block startup.
    try:
        async with engine.begin() as conn:
            await conn.execute(_sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
            for _tbl in ("knowledge_entries", "agent_memories", "skills"):
                await conn.execute(_sql_text(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS embedding vector(1024)"))
                await conn.execute(_sql_text(
                    f"CREATE INDEX IF NOT EXISTS ix_{_tbl}_embedding ON {_tbl} USING hnsw (embedding vector_cosine_ops)"
                ))
        logger.info("pgvector extension + embedding columns ensured (local bge-m3, 1024-dim)")
    except Exception as e:
        logger.warning(f"Could not ensure pgvector/embedding columns: {e}")

    # Second Brain MCP exposure columns: added to the model, but create_all never
    # ALTERs existing tables — ensure them idempotently so the MCP token endpoints
    # work on already-provisioned databases without a manual migration.
    try:
        async with engine.begin() as conn:
            await conn.execute(_sql_text(
                "ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS mcp_enabled boolean NOT NULL DEFAULT false"
            ))
            await conn.execute(_sql_text(
                "ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS mcp_token_encrypted text"
            ))
        logger.info("second_brains MCP columns ensured")
    except Exception as e:
        logger.warning(f"Could not ensure second_brains MCP columns: {e}")

    # Git-Abgleich je Vault (optional — ein Vault laeuft auch ganz ohne).
    try:
        async with engine.begin() as conn:
            for spalte in (
                "git_url text",
                "git_branch varchar(200)",
                "git_token_encrypted text",
                "git_last_sync_at timestamptz",
                "git_last_status varchar(255)",
            ):
                await conn.execute(_sql_text(
                    f"ALTER TABLE second_brains ADD COLUMN IF NOT EXISTS {spalte}"
                ))
        logger.info("second_brains git columns ensured")
    except Exception as e:
        logger.warning(f"Could not ensure second_brains git columns: {e}")

    # Einzelfreigabe fuer eigene KI-Abos (Kundenvorgabe 18.08.2026).
    try:
        async with engine.begin() as conn:
            await conn.execute(_sql_text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "allow_personal_credentials boolean NOT NULL DEFAULT false"
            ))
        logger.info("users.allow_personal_credentials ensured")
    except Exception as e:
        logger.warning(f"Could not ensure second_brains git columns: {e}")

    # Agent clone origin: distributed copies of a "trained" source agent track it
    # via agents.source_agent_id. Ensure idempotently (create_all never ALTERs).
    try:
        async with engine.begin() as conn:
            await conn.execute(_sql_text(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS source_agent_id varchar"
            ))
        logger.info("agents.source_agent_id ensured")
    except Exception as e:
        logger.warning(f"Could not ensure agents.source_agent_id: {e}")

    # Einrichtungshaken normalisieren. Das Einrichtungsgespraech ist entfallen —
    # der Agent haelt sich an seine Vorlage. Ein Bestandsagent, der noch auf
    # `false` steht, koennte den Haken nie mehr bekommen: das Interview, das ihn
    # setzte, gibt es nicht mehr. Er bliebe in der Oberflaeche fuer immer als
    # "nicht eingerichtet" markiert. Einmalig geradeziehen, idempotent.
    try:
        async with engine.begin() as conn:
            res = await conn.execute(_sql_text(
                "UPDATE agents SET config = jsonb_set("
                "  config::jsonb, '{onboarding_complete}', 'true'::jsonb, true"
                ")::json "
                "WHERE coalesce((config->>'onboarding_complete')::boolean, false) IS NOT TRUE"
            ))
            if res.rowcount:
                logger.info("Einrichtungshaken normalisiert: %d Agent(en)", res.rowcount)
    except Exception as e:
        logger.warning(f"Could not normalise onboarding_complete: {e}")

    await engine.dispose()

    result = subprocess.run(
        ["alembic", "stamp", "head"],
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        logger.info("Alembic stamped to HEAD")
    else:
        logger.warning(f"Alembic stamp failed: {result.stderr.strip()[:200]}")


async def _import_container_skills(docker_service) -> None:
    """Scan all running agent containers for SKILL.md files and persist to DB."""
    import re
    import logging
    logger = logging.getLogger(__name__)
    await asyncio.sleep(5)  # wait for DB to be ready
    try:
        from app.db.session import async_session_factory
        from app.models.skill import Skill, SkillStatus
        from sqlalchemy import select

        containers = docker_service.list_agent_containers()
        imported = 0
        async with async_session_factory() as db:
            for container in containers:
                name = container.get("name", "")
                if "agent" not in name.lower():
                    continue
                container_id = container.get("id", "")
                if not container_id:
                    continue
                try:
                    _, output = docker_service.exec_in_container(
                        container_id,
                        "find /workspace -name SKILL.md 2>/dev/null || true",
                    )
                    paths = [p.strip() for p in output.strip().splitlines() if p.strip()]
                    for path in paths:
                        try:
                            _, raw = docker_service.exec_in_container(container_id, f"cat '{path}'")
                            # Parse frontmatter
                            fm_match = re.match(r"^---\s*\n(.*?)\n---", raw, re.DOTALL)
                            fm = {}
                            if fm_match:
                                for line in fm_match.group(1).strip().split("\n"):
                                    if ":" in line:
                                        k, _, v = line.partition(":")
                                        fm[k.strip()] = v.strip().strip('"').strip("'")
                            parts = path.replace("SKILL.md", "").strip("/").split("/")
                            skill_name = fm.get("name") or (parts[-1] if parts[-1] else parts[-2])
                            if not skill_name:
                                continue
                            existing = (await db.execute(
                                select(Skill).where(Skill.name == skill_name)
                            )).scalar_one_or_none()
                            if not existing:
                                body = re.sub(r"^---.*?---\s*", "", raw, flags=re.DOTALL).strip()
                                skill = Skill(
                                    name=skill_name,
                                    description=fm.get("description", ""),
                                    content=body,
                                    category=fm.get("category", "tools"),
                                    status=SkillStatus.ACTIVE,
                                    created_by="import:container",
                                )
                                db.add(skill)
                                imported += 1
                        except Exception:
                            continue
                except Exception:
                    continue
            if imported:
                await db.commit()
                logger.info(f"Imported {imported} skills from agent containers into DB")
    except Exception as e:
        logging.getLogger(__name__).warning(f"Container skill import failed: {e}")


# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate config on startup
    _validate_config()

    # Repair /shared ownership/mode so agents (uid 1000) can write to it
    # (Docker creates the volume root:root 0755 -> agents can only read).
    from app.core.shared_volume import ensure_shared_volume_perms
    ensure_shared_volume_perms()

    # Mirror WARNING+ logs (redacted) to /shared/platform-errors.log so agents can
    # read platform errors from the shared volume and help fix the platform.
    from app.core.platform_error_log import setup_console_logging, setup_platform_error_log

    # Zuerst die Konsole: ohne Ausgabe-Handler war alles unterhalb von WARNING
    # unsichtbar, und eine Diagnose anhand fehlender Log-Zeilen ist Raten.
    _console_level = setup_console_logging()
    logger.info("Konsolen-Logging aktiv (Stufe %s)",
                logging.getLevelName(_console_level))

    if setup_platform_error_log():
        logger.info("Platform error log active -> /shared/platform-errors.log (secret-redacted)")

    # Run Alembic migrations to create/update tables
    # If Alembic fails (fresh DB, broken migration chain), fall back to
    # creating tables directly from SQLAlchemy models + stamp HEAD.
    import subprocess
    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd="/app",
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"Alembic migration failed: {result.stderr.strip()[:200]}")
            logger.info("Falling back to direct table creation from models ...")
            await _init_db_from_models()
        else:
            logger.info("Database migrations applied successfully")
    except subprocess.TimeoutExpired:
        logger.warning("Alembic migration timed out, falling back to direct init ...")
        await _init_db_from_models()

    # Ensure the oauth_clients table (built-in MCP authorization server) exists on
    # every startup, independent of Alembic — no migration ships for it. Idempotent.
    try:
        from app.db.session import engine as _eng
        from sqlalchemy import text as _txt
        async with _eng.begin() as conn:
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS oauth_clients ("
                "client_id varchar(64) PRIMARY KEY, "
                "client_secret_hash varchar(128), "
                "client_name varchar(255), "
                "redirect_uris text, "
                "grant_types varchar(255), "
                "token_endpoint_auth_method varchar(32), "
                "scope text, "
                "created_at timestamptz NOT NULL DEFAULT now(), "
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
        logger.info("oauth_clients table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure oauth_clients table: {e}")

    # Skill sources (issue #371): admin-managed crawl sources. Added as a model, but
    # create_all only runs in the fresh-DB fallback — ensure it on every startup so
    # the admin API + crawler work on already-provisioned DBs. `kind` as varchar (the
    # model's Enum accepts the string value); IF NOT EXISTS skips it where create_all
    # already made the native-enum column. Idempotent.
    try:
        from app.db.session import engine as _eng
        from sqlalchemy import text as _txt
        async with _eng.begin() as conn:
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS skill_sources ("
                "id serial PRIMARY KEY, "
                "name varchar NOT NULL, "
                "kind varchar NOT NULL DEFAULT 'github', "
                "location varchar NOT NULL, "
                "ref varchar, "
                "subdir varchar, "
                "credential_encrypted text, "
                "enabled boolean NOT NULL DEFAULT true, "
                "trusted boolean NOT NULL DEFAULT false, "
                "created_by varchar DEFAULT 'admin', "
                "last_crawled_at timestamptz, "
                "last_status varchar, "
                "created_at timestamptz NOT NULL DEFAULT now(), "
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
        logger.info("skill_sources table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure skill_sources table: {e}")

    # Memory auto-linker columns (#157): added to AgentMemoryLink, but create_all
    # never ALTERs an existing table. Ensure idempotently so the /related endpoint
    # and the semantic memory graph work on already-provisioned DBs.
    try:
        from app.db.session import engine as _eng
        from sqlalchemy import text as _txt
        async with _eng.begin() as conn:
            await conn.execute(_txt(
                "ALTER TABLE agent_memory_links ADD COLUMN IF NOT EXISTS similarity double precision"
            ))
            await conn.execute(_txt(
                "ALTER TABLE agent_memory_links ADD COLUMN IF NOT EXISTS auto_generated boolean NOT NULL DEFAULT false"
            ))
        logger.info("agent_memory_links auto-link columns ensured")
    except Exception as e:
        logger.warning(f"Could not ensure agent_memory_links columns: {e}")

    # DLP egress rules (#388): new table; create_all is only a fresh-DB fallback, so
    # ensure it idempotently and seed the built-in global defaults once.
    try:
        from app.db.session import engine as _eng
        from sqlalchemy import text as _txt
        async with _eng.begin() as conn:
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS dlp_rules ("
                "id serial PRIMARY KEY, pii_class varchar(40) NOT NULL, agent_id varchar,"
                "action varchar(20) NOT NULL, enabled boolean NOT NULL DEFAULT true,"
                "created_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt(
                "CREATE INDEX IF NOT EXISTS ix_dlp_rules_class ON dlp_rules (pii_class)"
            ))
            # Seed global defaults only if the table has no global rows yet.
            existing = (await conn.execute(_txt(
                "SELECT count(*) FROM dlp_rules WHERE agent_id IS NULL"
            ))).scalar() or 0
            if existing == 0:
                from app.core.dlp import DEFAULT_ACTIONS
                for cls, act in DEFAULT_ACTIONS.items():
                    await conn.execute(
                        _txt("INSERT INTO dlp_rules (pii_class, agent_id, action, enabled) "
                             "VALUES (:c, NULL, :a, true)"),
                        {"c": cls, "a": act},
                    )
        logger.info("dlp_rules table ensured + defaults seeded")
    except Exception as e:
        logger.warning(f"Could not ensure dlp_rules table: {e}")

    # Golden-Tests (#391): zwei neue Tabellen. Getrennt, weil es zwei Dinge sind —
    # die Sammlung aendert sich selten und gehoert der Rolle, der Lauf entsteht
    # staendig und gehoert einem Agenten zu einem Zeitpunkt. In einer Tabelle wuerde
    # jede Ausfuehrung die Sammlung ueberschreiben, und der Vergleich mit "vorher"
    # waere weg — also genau das, wofuer es das Ganze gibt.
    try:
        from app.db.session import engine as _eng
        from sqlalchemy import text as _txt
        async with _eng.begin() as conn:
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS eval_sets ("
                "id varchar PRIMARY KEY, name varchar NOT NULL, role varchar NOT NULL DEFAULT '',"
                "description text NOT NULL DEFAULT '', version integer NOT NULL DEFAULT 1,"
                "items json NOT NULL DEFAULT '[]', user_id varchar,"
                "created_at timestamptz NOT NULL DEFAULT now(),"
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS eval_runs ("
                "id varchar PRIMARY KEY,"
                "set_id varchar REFERENCES eval_sets(id) ON DELETE CASCADE,"
                "set_version integer NOT NULL DEFAULT 1, agent_id varchar NOT NULL,"
                "status varchar(20) NOT NULL DEFAULT 'running', score double precision,"
                "passed integer NOT NULL DEFAULT 0, total integer NOT NULL DEFAULT 0,"
                "baseline_score double precision, regression boolean NOT NULL DEFAULT false,"
                "trigger varchar(20) NOT NULL DEFAULT 'manual', results json NOT NULL DEFAULT '[]',"
                "created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz)"
            ))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_eval_runs_agent ON eval_runs (agent_id)"))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_eval_runs_set ON eval_runs (set_id)"))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_eval_sets_role ON eval_sets (role)"))
        logger.info("eval_sets + eval_runs tables ensured")
    except Exception as e:
        logger.warning(f"Could not ensure eval tables: {e}")

    # Workflow engine (#392): new tables; create_all is only a fresh-DB fallback.
    try:
        from app.db.session import engine as _eng
        from sqlalchemy import text as _txt
        async with _eng.begin() as conn:
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS workflows ("
                "id varchar PRIMARY KEY, name varchar NOT NULL, user_id varchar,"
                "enabled boolean NOT NULL DEFAULT true, definition json NOT NULL DEFAULT '{}',"
                "trigger json, created_at timestamptz NOT NULL DEFAULT now(),"
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS workflow_runs ("
                "id varchar PRIMARY KEY,"
                "workflow_id varchar REFERENCES workflows(id) ON DELETE CASCADE,"
                "status varchar(20) NOT NULL DEFAULT 'running', context json NOT NULL DEFAULT '{}',"
                "current_step varchar, current_task_id varchar, resume_at timestamptz,"
                "steps_done integer NOT NULL DEFAULT 0, error text,"
                "started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz)"
            ))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_workflow_runs_wf ON workflow_runs (workflow_id)"))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_workflow_runs_status ON workflow_runs (status)"))
            # Organisation (#394-org): folders + sharing.
            await conn.execute(_txt("ALTER TABLE workflows ADD COLUMN IF NOT EXISTS folder_id varchar"))
            # Webhook-Ausloeser koennen einen Workflow starten statt eines
            # Einzelauftrags (#392). Leer = Auftrag wie bisher.
            await conn.execute(_txt(
                "ALTER TABLE event_triggers ADD COLUMN IF NOT EXISTS workflow_id varchar"
            ))
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS workflow_folders ("
                "id varchar PRIMARY KEY, name varchar NOT NULL, user_id varchar NOT NULL,"
                "created_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS workflow_shares ("
                "id varchar PRIMARY KEY, workflow_id varchar, folder_id varchar,"
                "user_id varchar NOT NULL, role varchar(20) NOT NULL DEFAULT 'viewer',"
                "granted_by varchar, created_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_wf_shares_user ON workflow_shares (user_id)"))
            # Tagesplan eines Agenten (was er sich VORGENOMMEN hat, nicht was erledigt ist).
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS agent_plan_items ("
                "id serial PRIMARY KEY, agent_id varchar NOT NULL, plan_date date NOT NULL,"
                "title varchar NOT NULL, notes text DEFAULT '',"
                "planned_start timestamptz, estimated_minutes integer NOT NULL DEFAULT 30,"
                "source varchar(20) NOT NULL DEFAULT 'self',"
                "status varchar(20) NOT NULL DEFAULT 'planned',"
                "todo_id integer, task_id varchar,"
                "created_at timestamptz NOT NULL DEFAULT now(),"
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt(
                "CREATE INDEX IF NOT EXISTS ix_plan_items_agent_date "
                "ON agent_plan_items (agent_id, plan_date)"
            ))
            # Vorlagen bringen Daueraufgaben mit (V5).
            await conn.execute(_txt(
                "ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS "
                "responsibilities json DEFAULT '[]'::json"
            ))
            await conn.execute(_txt(
                "ALTER TABLE agent_plan_items ADD COLUMN IF NOT EXISTS "
                "priority varchar(10) NOT NULL DEFAULT 'normal'"
            ))
            await conn.execute(_txt(
                "ALTER TABLE agent_plan_items ADD COLUMN IF NOT EXISTS schedule_id varchar"
            ))
            # Web-Push-Anmeldungen der Browser — das Gegenstueck zu device_tokens (iOS).
            # Der Endpunkt ist eindeutig, damit ein erneut angemeldeter Browser den
            # bestehenden Eintrag auffrischt statt Meldungen doppelt zu bekommen.
            await conn.execute(_txt(
                "CREATE TABLE IF NOT EXISTS push_subscriptions ("
                "id serial PRIMARY KEY, user_id varchar NOT NULL,"
                "endpoint text NOT NULL UNIQUE, p256dh varchar NOT NULL,"
                "auth varchar NOT NULL, user_agent varchar,"
                "created_at timestamptz NOT NULL DEFAULT now(),"
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt(
                "CREATE INDEX IF NOT EXISTS ix_push_subscriptions_user "
                "ON push_subscriptions (user_id)"
            ))
            await conn.execute(_txt("CREATE INDEX IF NOT EXISTS ix_wf_folders_user ON workflow_folders (user_id)"))
        logger.info("workflow tables ensured")
    except Exception as e:
        logger.warning(f"Could not ensure workflow tables: {e}")

    # App-Freigaben (#467): wer darf eine Agenten-App öffnen. Ohne Zeile hier gilt
    # weiterhin deny — nur der Besitzer kommt rein. Idempotent, wie oben ohne Alembic.
    try:
        from app.db.session import engine as _eng_as
        from sqlalchemy import text as _txt_as
        async with _eng_as.begin() as conn:
            await conn.execute(_txt_as(
                "CREATE TABLE IF NOT EXISTS app_shares ("
                "id varchar PRIMARY KEY, project varchar NOT NULL, agent_id varchar NOT NULL,"
                "scope varchar(20) NOT NULL DEFAULT 'user', user_id varchar, token_hash varchar,"
                "expires_at timestamptz, created_by varchar,"
                "created_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_as("CREATE INDEX IF NOT EXISTS ix_app_shares_project ON app_shares (project)"))
            await conn.execute(_txt_as("CREATE INDEX IF NOT EXISTS ix_app_shares_user ON app_shares (user_id)"))
            await conn.execute(_txt_as("CREATE INDEX IF NOT EXISTS ix_app_shares_agent ON app_shares (agent_id)"))
            # Der Token-Hash muss eindeutig sein, sonst könnte eine Kollision auf die
            # falsche App zeigen. (In der Tabelle steht NUR der Hash, nie der Token.)
            await conn.execute(_txt_as("CREATE UNIQUE INDEX IF NOT EXISTS ux_app_shares_token ON app_shares (token_hash)"))
            # Verschlüsselter Token, damit der Besitzer den Link später noch einmal
            # sehen kann. Bestehende Zeilen bleiben leer — dort ist der Klartext weg.
            await conn.execute(_txt_as("ALTER TABLE app_shares ADD COLUMN IF NOT EXISTS token_enc varchar"))
        logger.info("app_shares table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure app_shares table: {e}")

    # Eigene Menuepunkte (fremde Seiten im Rahmen oder als Link). Wie bei den
    # uebrigen jungen Tabellen idempotent beim Start statt per Migration — eine
    # Bestandsinstallation kaeme sonst ohne Handanlegen nicht an die Funktion.
    try:
        from app.db.session import engine as _eng_cp
        from sqlalchemy import text as _txt_cp
        async with _eng_cp.begin() as conn:
            await conn.execute(_txt_cp(
                "CREATE TABLE IF NOT EXISTS custom_pages ("
                "id serial PRIMARY KEY, slug varchar(64) NOT NULL UNIQUE, title varchar(120) NOT NULL,"
                "description varchar(400), url text NOT NULL,"
                "icon varchar(60) NOT NULL DEFAULT 'Globe',"
                "group_key varchar(20) NOT NULL DEFAULT 'collab',"
                "open_mode varchar(10) NOT NULL DEFAULT 'iframe',"
                "sort_order integer NOT NULL DEFAULT 0,"
                "enabled boolean NOT NULL DEFAULT true,"
                "allow_media boolean NOT NULL DEFAULT false,"
                "created_by varchar,"
                "created_at timestamptz NOT NULL DEFAULT now(),"
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_cp(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_custom_pages_slug ON custom_pages (slug)"
            ))
        logger.info("custom_pages table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure custom_pages table: {e}")

    # IdP-Gruppen auf Rollen (SAML + Microsoft-OIDC, vereinheitlicht) + die dabei
    # gesehenen Gruppennamen. Zwei Tabellen, weil es zwei verschiedene Dinge sind —
    # eine Zuordnung aendert sich selten und ist eine bewusste Admin-Entscheidung,
    # eine Beobachtung entsteht bei jedem Login von selbst.
    try:
        from app.db.session import engine as _eng_sso
        from sqlalchemy import text as _txt_sso
        async with _eng_sso.begin() as conn:
            await conn.execute(_txt_sso(
                "CREATE TABLE IF NOT EXISTS sso_group_role_mappings ("
                "id serial PRIMARY KEY, provider varchar(20) NOT NULL,"
                "group_name varchar(200) NOT NULL, target_kind varchar(20) NOT NULL,"
                "target_value varchar(40) NOT NULL, priority integer NOT NULL DEFAULT 0,"
                "created_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_sso(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_sso_group_role_provider_group "
                "ON sso_group_role_mappings (provider, group_name)"
            ))
            await conn.execute(_txt_sso(
                "CREATE TABLE IF NOT EXISTS sso_observed_groups ("
                "id serial PRIMARY KEY, provider varchar(20) NOT NULL,"
                "group_name varchar(200) NOT NULL,"
                "first_seen_at timestamptz NOT NULL DEFAULT now(),"
                "last_seen_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_sso(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_sso_observed_provider_group "
                "ON sso_observed_groups (provider, group_name)"
            ))
        logger.info("sso_group_role_mappings + sso_observed_groups tables ensured")
    except Exception as e:
        logger.warning(f"Could not ensure sso_group_role_mappings tables: {e}")

    # Eigener Claude-/Codex-Zugang je Nutzer. Getrennte Zugaenge = getrennte
    # Token-Familien: die Rotation des einen kann die des anderen nicht mehr
    # umbringen (der Grund, weshalb Codex-Recreates bis heute serialisiert werden).
    try:
        from app.db.session import engine as _eng_uc
        from sqlalchemy import text as _txt_uc
        async with _eng_uc.begin() as conn:
            await conn.execute(_txt_uc(
                "CREATE TABLE IF NOT EXISTS user_ai_credentials ("
                "id serial PRIMARY KEY, user_id varchar NOT NULL,"
                "harness varchar(20) NOT NULL, secret_encrypted text NOT NULL,"
                "label varchar(120), last_status varchar(32),"
                "last_used_at timestamptz,"
                "created_at timestamptz NOT NULL DEFAULT now(),"
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_uc(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_ai_credential "
                "ON user_ai_credentials (user_id, harness)"
            ))
            await conn.execute(_txt_uc(
                "CREATE INDEX IF NOT EXISTS ix_user_ai_credentials_user "
                "ON user_ai_credentials (user_id)"
            ))
        logger.info("user_ai_credentials table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure app_shares table: {e}")

    # Ensure the chat_sessions table (per-chat title/pin metadata) on every
    # startup, independent of Alembic (10 heads → `upgrade head` may not run the
    # create-all fallback). Idempotent. Without it, get_chat_sessions 500s.
    try:
        from app.db.session import engine as _eng_cs
        from sqlalchemy import text as _txt_cs
        async with _eng_cs.begin() as conn:
            await conn.execute(_txt_cs(
                "CREATE TABLE IF NOT EXISTS chat_sessions ("
                "id serial PRIMARY KEY, "
                "agent_id varchar NOT NULL, "
                "session_id varchar NOT NULL, "
                "title text, "
                "pinned boolean NOT NULL DEFAULT false, "
                "reasoning_level varchar, "
                "created_at timestamptz NOT NULL DEFAULT now(), "
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_cs(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_sessions_agent_session "
                "ON chat_sessions (agent_id, session_id)"
            ))
            await conn.execute(_txt_cs(
                "CREATE INDEX IF NOT EXISTS ix_chat_sessions_agent_id ON chat_sessions (agent_id)"
            ))
            await conn.execute(_txt_cs(
                "CREATE INDEX IF NOT EXISTS ix_chat_sessions_session_id ON chat_sessions (session_id)"
            ))
            # v1.234.0: per-chat reasoning level (NULL = Auto / harness default)
            await conn.execute(_txt_cs(
                "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS reasoning_level varchar"
            ))
        logger.info("chat_sessions table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure chat_sessions table: {e}")

    # Ensure the job_state table (long-running job checkpoints + auto-resume) on
    # every startup, independent of Alembic — no migration ships for it, and on an
    # already-provisioned DB `alembic upgrade head` succeeds so the create_all
    # fallback never runs. Idempotent. Without it, the startup recovery hook 500s
    # with relation "job_state" does not exist on every restart.
    try:
        from app.db.session import engine as _eng_js
        from sqlalchemy import text as _txt_js
        async with _eng_js.begin() as conn:
            await conn.execute(_txt_js(
                "CREATE TABLE IF NOT EXISTS job_state ("
                "id varchar PRIMARY KEY, "
                "kind varchar(100) NOT NULL, "
                "ref_id varchar, "
                "step varchar NOT NULL DEFAULT '', "
                "progress_pct double precision NOT NULL DEFAULT 0.0, "
                "status varchar(20) NOT NULL DEFAULT 'running', "
                "last_heartbeat timestamptz NOT NULL DEFAULT now(), "
                "resume_count integer NOT NULL DEFAULT 0, "
                "error text, "
                "job_metadata json NOT NULL DEFAULT '{}'::json, "
                "created_at timestamptz NOT NULL DEFAULT now(), "
                "updated_at timestamptz NOT NULL DEFAULT now())"
            ))
            await conn.execute(_txt_js(
                "CREATE INDEX IF NOT EXISTS ix_job_state_ref_id ON job_state (ref_id)"
            ))
            await conn.execute(_txt_js(
                "CREATE INDEX IF NOT EXISTS ix_job_state_status_heartbeat "
                "ON job_state (status, last_heartbeat)"
            ))
        logger.info("job_state table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure job_state table: {e}")

    # External MCP servers: optional custom auth headers and persisted discovery
    # health. Ensured on every startup, independent of Alembic (the migration chain
    # is multi-head — no new migrations ship; see the reflection/job_state ensures
    # above). Idempotent, so existing databases receive new columns after deploy.
    try:
        from app.db.session import engine as _eng_mh
        from sqlalchemy import text as _txt_mh
        async with _eng_mh.begin() as conn:
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS headers_encrypted text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS last_checked_at timestamptz"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS last_status varchar(32)"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS last_error varchar(255)"
            ))
            await conn.execute(_txt_mh(
                # Pro Server zugelassene private Adresse (statt global per Umgebung).
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS "
                "allow_private_host boolean NOT NULL DEFAULT false"
            ))
            # Client-side OAuth columns (#426).
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_enabled boolean DEFAULT false"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_authorization_endpoint text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_token_endpoint text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_registration_endpoint text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_scope text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_resource text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_client_id varchar"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_client_secret_encrypted text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_refresh_token_encrypted text"
            ))
            await conn.execute(_txt_mh(
                "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS oauth_access_expires_at timestamptz"
            ))
        logger.info("mcp_servers auth/header + health + oauth columns ensured")
    except Exception as e:
        logger.warning(f"Could not ensure mcp_servers columns: {e}")

    # AI accounts: model-discovery health columns (#435), mirroring mcp_servers.
    try:
        from app.db.session import engine as _eng_ai
        from sqlalchemy import text as _txt_ai
        async with _eng_ai.begin() as conn:
            await conn.execute(_txt_ai(
                "ALTER TABLE ai_accounts ADD COLUMN IF NOT EXISTS last_checked_at timestamptz"
            ))
            await conn.execute(_txt_ai(
                "ALTER TABLE ai_accounts ADD COLUMN IF NOT EXISTS last_status varchar(32)"
            ))
            await conn.execute(_txt_ai(
                "ALTER TABLE ai_accounts ADD COLUMN IF NOT EXISTS last_error varchar(255)"
            ))
        logger.info("ai_accounts health columns ensured")
    except Exception as e:
        logger.warning(f"Could not ensure ai_accounts columns: {e}")

    # Reflection/"Dreaming": provenance column + run-log table. Ensured on every
    # startup, independent of Alembic (multi-head chain → no new migrations).
    try:
        from app.db.session import engine as _eng_rf
        from sqlalchemy import text as _txt_rf
        async with _eng_rf.begin() as conn:
            await conn.execute(_txt_rf(
                "ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS source varchar(30)"
            ))
            await conn.execute(_txt_rf(
                "CREATE INDEX IF NOT EXISTS ix_agent_memories_source ON agent_memories (source)"
            ))
            await conn.execute(_txt_rf(
                "CREATE TABLE IF NOT EXISTS reflection_runs ("
                "id serial PRIMARY KEY, "
                "started_at timestamptz NOT NULL DEFAULT now(), "
                "finished_at timestamptz, "
                "status varchar(30) NOT NULL DEFAULT 'running', "
                "mode varchar(20) NOT NULL DEFAULT 'hybrid', "
                "\"trigger\" varchar(20) NOT NULL DEFAULT 'scheduled', "
                "stats json NOT NULL DEFAULT '{}'::json, "
                "tokens_used integer NOT NULL DEFAULT 0, "
                "cost_usd double precision, "
                "error text)"
            ))
            await conn.execute(_txt_rf(
                "CREATE INDEX IF NOT EXISTS ix_reflection_runs_started_at ON reflection_runs (started_at)"
            ))
        logger.info("reflection: agent_memories.source + reflection_runs ensured")
    except Exception as e:
        logger.warning(f"Could not ensure reflection tables: {e}")

    # Ensure users.approved (admin-approval gate). Default true so existing users stay
    # usable; new self-registered users get false when require_user_approval is on.
    try:
        from app.db.session import engine as _eng2
        from sqlalchemy import text as _txt2
        async with _eng2.begin() as conn:
            await conn.execute(_txt2(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS approved boolean NOT NULL DEFAULT true"
            ))
        logger.info("users.approved column ensured")
    except Exception as e:
        logger.warning(f"Could not ensure users.approved: {e}")

    # Seed autonomy preset rules (defaults per level into DB if not yet present)
    try:
        from app.api.approval_rules import seed_autonomy_presets
        from app.db.session import async_session_factory as _sf_presets

        async with _sf_presets() as db:
            await seed_autonomy_presets(db)
        logger.info("Autonomy preset rules seeded")
    except Exception as e:
        logger.warning(f"Failed to seed autonomy presets: {e}")

    # Seed URL allowlist templates (builtin templates into DB if not yet present)
    try:
        from app.api.url_allowlist import seed_url_allowlist_templates
        from app.db.session import async_session_factory as _sf_url

        async with _sf_url() as db:
            await seed_url_allowlist_templates(db)
        logger.info("URL allowlist templates seeded")
    except Exception as e:
        logger.warning(f"Failed to seed URL allowlist templates: {e}")

    # Einmalige Bereinigung: die alte Vorgabe 4096 aus den Agenten-Konfigurationen
    # entfernen. Sie stand dort nicht, weil jemand sie gewollt hat, sondern weil sie
    # die Vorgabe war — und sie kappt lange Antworten mitten im Satz.
    # Bewusst NUR exakt 4096: wer eine andere Zahl eingetragen hat, hat sich etwas
    # dabei gedacht, und die bleibt unangetastet.
    try:
        from sqlalchemy import select as _sel_mt
        from sqlalchemy.orm.attributes import flag_modified as _flag_mt

        from app.db.session import async_session_factory as _sf_mt
        from app.models.agent import Agent as _AgentMT

        async with _sf_mt() as db:
            cleared = 0
            for agent in (await db.execute(_sel_mt(_AgentMT))).scalars().all():
                cfg = agent.llm_config or {}
                if cfg.get("max_tokens") == 4096:
                    cfg = dict(cfg)
                    cfg["max_tokens"] = 0
                    agent.llm_config = cfg
                    _flag_mt(agent, "llm_config")
                    cleared += 1
            if cleared:
                await db.commit()
                logger.info(
                    "Antwortlaengen-Grenze bei %d Agenten entfernt (alte Vorgabe 4096)",
                    cleared,
                )
    except Exception as e:
        logger.warning(f"Failed to clear legacy max_tokens: {e}")

    # Seed builtin eval sets (#193): Team-Grundlagen + Angriffsfaelle.
    # Nur anlegen, nie ueberschreiben — wer eine Sammlung angepasst hat, soll sie
    # beim naechsten Start nicht zurueckgesetzt bekommen.
    try:
        from sqlalchemy import select as _select

        from app.core.eval_seeds import BUILTIN_EVAL_SETS
        from app.db.session import async_session_factory as _sf_evals
        from app.models.eval_set import EvalSet as _EvalSet

        async with _sf_evals() as db:
            created = 0
            for spec in BUILTIN_EVAL_SETS:
                exists = (await db.execute(
                    _select(_EvalSet).where(_EvalSet.id == spec["id"])
                )).scalar_one_or_none()
                if exists is not None:
                    continue
                db.add(_EvalSet(
                    id=spec["id"], name=spec["name"], role=spec.get("role", ""),
                    description=spec.get("description", ""), items=spec["items"],
                ))
                created += 1
            if created:
                await db.commit()
        logger.info("Builtin eval sets seeded (%d new)", created)
    except Exception as e:
        logger.warning(f"Failed to seed builtin eval sets: {e}")

    # Seed builtin agent templates
    try:
        from app.core.agent_templates import BUILTIN_TEMPLATES
        from app.db.session import async_session_factory
        from app.models.agent_template import AgentTemplate

        async with async_session_factory() as db:
            from sqlalchemy import select as sel
            for tmpl_data in BUILTIN_TEMPLATES:
                existing = await db.scalar(
                    sel(AgentTemplate).where(AgentTemplate.name == tmpl_data["name"])
                )
                if not existing:
                    # Mitgelieferte Vorlagen sind sofort sichtbar. Ohne das
                    # griff die Vorgabe des Modells (``is_published=False``)
                    # und JEDER Nicht-Administrator sah beim Anlegen eines
                    # Agenten „Noch keine Vorlagen angelegt" — obwohl 31
                    # Vorlagen in der Datenbank standen (beobachtet 2026-08-16).
                    # Der Entwurf-/Veroeffentlichen-Ablauf ist fuer die selbst
                    # geschriebenen Vorlagen des Administrators gedacht, nicht
                    # fuer die, die mit dem Produkt kommen.
                    tmpl = AgentTemplate(
                        is_builtin=True,
                        is_published=True,
                        published_at=datetime.now(timezone.utc),
                        **tmpl_data,
                    )
                    db.add(tmpl)
                elif existing.is_builtin:
                    # Update builtin templates if source has changed
                    for field in (
                        "display_name", "description", "role", "permissions",
                        "integrations", "knowledge_template", "claude_md",
                        "icon", "category", "model", "skill_ids", "mcp_server_ids",
                    ):
                        source_val = tmpl_data.get(field)
                        if source_val is not None and getattr(existing, field) != source_val:
                            setattr(existing, field, source_val)
            await db.commit()
        logger.info(f"Seeded/synced {len(BUILTIN_TEMPLATES)} builtin agent templates")
    except Exception as e:
        logger.warning(f"Failed to seed templates: {e}")

    # Bestehende Anlagen nachziehen: dort stehen die mitgelieferten Vorlagen auf
    # „nicht veroeffentlicht" und sind fuer Nicht-Administratoren unsichtbar.
    # Die Korrektur im Seeder oben erreicht sie nicht, weil sie schon existieren.
    try:
        from app.core.agent_templates import publish_builtin_templates_once
        from app.db.session import async_session_factory as _sf_pub

        async with _sf_pub() as db:
            anzahl = await publish_builtin_templates_once(db)
        if anzahl:
            logger.info(
                "%d mitgelieferte Vorlagen nachtraeglich veroeffentlicht — "
                "sie waren fuer Nicht-Administratoren unsichtbar", anzahl,
            )
    except Exception as e:
        logger.warning(f"Failed to publish builtin templates: {e}")

    # Seed builtin skills (feierabend, morning_briefing, daily_log_check)
    try:
        from app.db.session import async_session_factory as _sf_skills
        from app.models.skill import Skill, SkillStatus, SkillCategory
        from sqlalchemy import select as _sel_skills

        _BUILTIN_SKILLS = [
            {
                "name": "feierabend",
                "description": "End-of-day skill: summarises the daily log, marks open items, updates agent state. Run when the workday ends.",
                "category": SkillCategory.ROUTINE if hasattr(SkillCategory, "ROUTINE") else "routine",
                "content": """\
# Feierabend Skill

Use this at the end of every workday to close out the daily log.

## Steps

1. Read today's daily log:
```bash
cat /workspace/daily/$(date +%Y-%m-%d).md 2>/dev/null || echo "(no entries today)"
```

2. Write a clean summary + open items section **at the bottom** of today's log:
```bash
DATE=$(date +%Y-%m-%d)
cat >> /workspace/daily/${DATE}.md << 'FEIERABEND'

## Summary
<2-3 sentences: what was accomplished today>

## Open Items
- [ ] <unfinished task 1>
- [ ] <unfinished task 2>
FEIERABEND
```

3. Update `/workspace/.agent_state.md` — set **Next Steps** to the open items from above.

4. Confirm to the user: "Feierabend! Tageslog unter /workspace/daily/DATE.md abgeschlossen. N offene Punkte für morgen gespeichert."

## Rules
- Never mark something as done if it wasn't actually completed.
- If there are no open items, say so explicitly — don't invent tasks.
- Keep the summary factual and short.
""",
            },
            {
                "name": "morning_briefing",
                "description": "Start-of-day skill: reads the last 5 daily logs, lists all open items, and presents a focused briefing for the new day.",
                "category": SkillCategory.ROUTINE if hasattr(SkillCategory, "ROUTINE") else "routine",
                "content": """\
# Morning Briefing Skill

Run this at the start of every workday before taking any user requests.

## Steps

1. Check open items from the last 5 days:
```bash
ls /workspace/daily/*.md 2>/dev/null | sort | tail -5 | while read f; do
  echo "=== $(basename $f) ==="; grep -A 30 "## Open Items" "$f" 2>/dev/null || echo "(no open items)"; echo
done
```

2. Read today's knowledge context:
```bash
cat /workspace/knowledge.md 2>/dev/null | head -60
```

3. Call `brain_search` with a query about recent work and priorities.

4. Call `memory_search` with room matching the active channel.

5. Present a compact briefing to the user:
```
Guten Morgen! Hier dein Briefing:

**Offene Punkte aus den letzten Tagen:**
- [ ] <item from day X>
- [ ] <item from day Y>

**Heutiger Fokus:** <1 sentence based on agent_state.md Next Steps>

Womit sollen wir starten?
```

## Rules
- Skip days with no log file (don't error, just continue).
- List only genuinely open items (not already completed ones).
- Keep the briefing concise — max 10 open items, group by topic if needed.
""",
            },
            {
                "name": "secondbrain_lookup",
                "description": "Second Brain: search the shared department knowledge vault (Markdown under /mnt/brains/*) before answering support/how-to/troubleshooting questions, cite the source, and contribute new learnings back.",
                "category": SkillCategory.WORKFLOW if hasattr(SkillCategory, "WORKFLOW") else "WORKFLOW",
                "content": """\
# Second Brain Lookup Skill

A shared **department knowledge base** may be mounted into this agent as a
Markdown vault under `/mnt/brains/<name>/` (e.g. `/mnt/brains/it_operations/`).
It is the single source of truth for department know-how (runbooks, error-code
fixes, how-tos). Use it whenever a question could be answered from documented
knowledge — especially support, troubleshooting and "how do I…" questions.

## When to use
- The user reports an error code (e.g. `x17137`), a device/system problem, or asks "how do I…".
- Any factual department question that is likely documented.

## 1. Find the vault(s)
```bash
ls -d /mnt/brains/*/ 2>/dev/null || echo "(no Second Brain mounted)"
```
If none is mounted, answer normally (no department vault assigned to this agent).

## 2. Search FIRST (before answering)
Grep for the concrete keywords / error code, then read the matches:
```bash
Q="x17137"   # the user's error code / keywords
grep -ril "$Q" /mnt/brains/*/ 2>/dev/null | head
```
Open the best matches with `read_file` and answer **from their content**. Always
**cite the source file** (e.g. "laut `it_operations/Drucker/x17137.md`"). If grep
finds nothing, broaden the terms (synonyms, German+English) before giving up.

## 3. Contribute back (if you have write access)
If you learned something new, or fixed a problem that wasn't documented, add or
update a concise article so the whole department benefits:
- One `.md` per topic, **Wikimedia-style** folders (`Drucker/`, `Netzwerk/`, `Zugaenge/`).
- Speaking file names; put error codes / keywords in plain text so grep finds them.
- Link related articles with `[[Titel]]`.
- Update the vault's `index.md` to link the new article.
```bash
# only if the mount is writable (rw)
mkdir -p /mnt/brains/it_operations/Drucker
write_file /mnt/brains/it_operations/Drucker/x17137.md  # title + cause + step-by-step fix
```
File history is versioned automatically (local git on the server) — just write
clean Markdown; you don't need to commit.

## Rules
- **Search before you answer** — never guess if the vault might hold the answer.
- Cite the source `.md`. Don't invent file names.
- Only write if the mount is read-write; never delete others' articles.
- Keep articles short, factual, and reusable.
""",
            },
        ]

        async with _sf_skills() as db:
            for skill_data in _BUILTIN_SKILLS:
                existing = await db.scalar(
                    _sel_skills(Skill).where(Skill.name == skill_data["name"])
                )
                if not existing:
                    db.add(Skill(
                        name=skill_data["name"],
                        description=skill_data["description"],
                        content=skill_data["content"],
                        category=skill_data["category"],
                        status=SkillStatus.ACTIVE,
                        created_by="builtin",
                    ))
                else:
                    # Always sync builtin skill content
                    existing.description = skill_data["description"]
                    existing.content = skill_data["content"]
                    existing.status = SkillStatus.ACTIVE
            await db.commit()
        logger.info(f"Seeded/synced {len(_BUILTIN_SKILLS)} builtin skills")
    except Exception as e:
        logger.warning(f"Failed to seed builtin skills: {e}")

    # Load persisted settings from DB
    try:
        from app.db.session import async_session_factory as _sf
        from app.services.settings_service import SettingsService

        async with _sf() as db:
            svc = SettingsService(db)
            await svc.load_into_config()
    except Exception as e:
        logger.warning(f"Could not load persisted settings: {e}")

    # Load license from DB (falls back to community tier if not present or invalid)
    try:
        from app.core.license import load_license_from_string
        from app.db.session import async_session_factory as _sf_lic_load
        from app.services.settings_service import SettingsService as _SS_lic

        async with _sf_lic_load() as db:
            svc = _SS_lic(db)
            license_key = await svc.get("license_key")
            load_license_from_string(license_key or "")
    except Exception as e:
        logger.warning(f"Could not load license: {e}")

    # Auto-detect Claude token from environment if not configured in DB
    try:
        from app.db.session import async_session_factory as _sf2
        from app.services.settings_service import SettingsService as _SS2

        async with _sf2() as db:
            svc = _SS2(db)
            has_api_key = bool(settings.anthropic_api_key)
            has_oauth = bool(settings.claude_code_oauth_token)

            if not has_api_key and not has_oauth:
                # Check env vars that might be passed from host
                env_oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
                env_api_key = os.environ.get("ANTHROPIC_API_KEY", "")

                if env_oauth:
                    settings.claude_code_oauth_token = env_oauth
                    await svc.set("claude_code_oauth_token", env_oauth)
                    await svc.set("model_provider", "anthropic")
                    await db.commit()
                    logger.info("Auto-detected CLAUDE_CODE_OAUTH_TOKEN from environment - saved to platform settings")
                elif env_api_key:
                    settings.anthropic_api_key = env_api_key
                    await svc.set("anthropic_api_key", env_api_key)
                    await svc.set("model_provider", "anthropic")
                    await db.commit()
                    logger.info("Auto-detected ANTHROPIC_API_KEY from environment - saved to platform settings")
                else:
                    logger.info("No Claude authentication found - configure in Settings page")
            else:
                logger.info(
                    f"Claude authentication configured: "
                    f"{'API Key' if has_api_key else 'OAuth Token'}"
                )

            # Auto-detect refresh token from environment
            env_refresh = os.environ.get("CLAUDE_CODE_OAUTH_REFRESH_TOKEN", "")
            if env_refresh and not settings.claude_code_oauth_refresh_token:
                settings.claude_code_oauth_refresh_token = env_refresh
                await svc.set("claude_code_oauth_refresh_token", env_refresh)
                await db.commit()
                logger.info("Auto-detected CLAUDE_CODE_OAUTH_REFRESH_TOKEN from environment")
    except Exception as e:
        logger.warning(f"Auto-detection of Claude token failed: {e}")

    # Load initial token from Keychain sync file (or fallback to env/DB)
    from app.services.claude_token_service import ClaudeTokenService

    token_svc = ClaudeTokenService()
    await token_svc.write_initial_token()
    logger.info("Claude token initialized (background sync every 2 min from Keychain file)")

    try:
        from app.services.codex_auth_service import CodexAuthService
        if await CodexAuthService().sync_auth_json():
            logger.info("Codex auth initialized from encrypted DB session")
    except Exception as e:
        logger.warning(f"Could not initialize Codex auth: {e}")

    # Initialize services
    app.state.redis = RedisService(settings.redis_url)
    await app.state.redis.connect()
    app.state.docker = DockerService()

    # Initialize stream manager for WebSocket
    init_stream_manager(app.state.redis, app.state.docker)

    # Initialize computer-use bridge session registry
    from app.api.computer_use import init_computer_use
    init_computer_use(app.state.redis)

    # Recover stale tasks from previous shutdown
    try:
        from app.core.load_balancer import LoadBalancer
        from app.core.task_router import TaskRouter
        from app.db.session import async_session_factory

        async with async_session_factory() as db:
            lb = LoadBalancer(app.state.redis)
            router = TaskRouter(db, app.state.redis, lb)
            recovered = await router.recover_stale_tasks(stale_minutes=10)
            if recovered:
                logger.info(f"Recovered {recovered} stale tasks from previous shutdown")
    except Exception as e:
        logger.warning(f"Stale task recovery failed: {e}")

    # Restart agent containers that were RUNNING before orchestrator shutdown
    # (containers get killed when orchestrator restarts via docker-compose rebuild)
    try:
        from app.db.session import async_session_factory as _sf_restart
        from app.models.agent import Agent, AgentState
        from sqlalchemy import select as _sel

        async with _sf_restart() as db:
            result = await db.execute(
                _sel(Agent).where(Agent.state.in_([AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING]))
            )
            previously_running = list(result.scalars().all())
            restarted = 0
            for agent in previously_running:
                if not agent.container_id:
                    continue
                container_status = app.state.docker.get_container_status(agent.container_id)
                if container_status in ("exited", "created", "paused"):
                    try:
                        app.state.docker.start_container(agent.container_id)
                        restarted += 1
                    except Exception as ex:
                        logger.warning(f"Could not restart agent {agent.name} ({agent.id}) on startup: {ex}")
                        agent.state = AgentState.STOPPED
                elif container_status == "unknown":
                    # Container gone entirely — mark as stopped, will be recreated on next use
                    agent.state = AgentState.STOPPED
            await db.commit()
            if restarted:
                logger.info(f"[Startup] Restarted {restarted} agent containers from previous session")

            # Anleitung in JEDEN lebenden Container nachziehen. Sie liegt im Container,
            # nicht im Repo — ohne das laesst ein reines `git pull` + Orchestrator-Neustart
            # (der uebliche Deploy-Weg, auch beim Kunden) alle laufenden Agenten mit der
            # alten Anleitung zurueck, und man muesste jeden einzeln von Hand aktualisieren.
            from app.core.agent_manager import AgentManager as _AM
            _mgr = _AM(db, app.state.docker, getattr(app.state, "redis", None))
            refreshed = 0
            for agent in previously_running:
                if await _mgr.refresh_instructions(agent):
                    refreshed += 1
            if refreshed:
                logger.info(f"[Startup] Refreshed instructions for {refreshed} agent(s)")
    except Exception as e:
        logger.warning(f"Agent startup recovery failed: {e}")

    # So oft wird eine unterbrochene Aufgabe hoechstens automatisch neu gestartet.
    _MAX_RESUMES = 3

    # Resume long-running jobs that were checkpointing before shutdown (issue #211).
    # Jobs with a fresh heartbeat are resumable; stale ones are marked crashed and
    # the user is alerted so no long job silently dies across a container restart.
    async def _resume_agent_task(job):
        """Re-enqueue an agent task interrupted mid-run by a container restart (#282).

        The original task (if still present) is marked failed and its stale queue
        entry dropped so it cannot also be picked up — the replacement is the only
        executor. The job row is deleted so a second restart never re-launches it.
        """
        from app.core.load_balancer import LoadBalancer
        from app.core.task_router import TaskRouter
        from app.db.session import async_session_factory as _sf_resume
        from app.models.task import Task, TaskStatus, is_terminal_task_status
        from app.services.job_state import delete_job
        from sqlalchemy import select as _sel

        meta = dict(job.job_metadata or {})
        prompt = meta.get("prompt")
        if not prompt:
            logger.warning(f"[Resume] Job {job.id} has no persisted prompt — cannot re-launch")
            async with _sf_resume() as db:
                await delete_job(db, job.id)
            return

        async with _sf_resume() as db:
            lb = LoadBalancer(app.state.redis)
            router = TaskRouter(db, app.state.redis, lb, docker_service=app.state.docker)

            orig = None
            if job.ref_id:
                orig = (await db.execute(_sel(Task).where(Task.id == job.ref_id))).scalar_one_or_none()

            # Already finished after the crash was recorded — nothing to redo.
            if orig is not None and orig.status == TaskStatus.COMPLETED:
                await delete_job(db, job.id)
                logger.info(f"[Resume] Job {job.id} original task {job.ref_id} already completed — skipping")
                return

            # Eine Fortsetzung kann selbst wieder unterbrochen werden — und ihre
            # Fortsetzung wieder. Jeder Anlauf faengt bei null an und kostet voll:
            # bei fuenf Neustarts hintereinander lief EIN Plan-Block fuenfmal
            # komplett durch (rund 14 USD statt knapp 4). Nach drei Anlaeufen wird
            # deshalb nicht mehr automatisch fortgesetzt, sondern gemeldet.
            resume_count = int(((orig.metadata_ or {}) if orig is not None else {})
                               .get("resume_count") or 0) + 1
            if resume_count > _MAX_RESUMES:
                if orig is not None and not is_terminal_task_status(orig.status):
                    orig.status = TaskStatus.FAILED
                    orig.error = (
                        f"Nach {_MAX_RESUMES} Fortsetzungen nicht weiter automatisch "
                        f"neu gestartet — jeder Anlauf beginnt von vorn."
                    )
                    orig.completed_at = datetime.now(timezone.utc)
                from app.models.notification import Notification
                db.add(Notification(
                    agent_id=meta.get("agent_id"),
                    type="warning",
                    title="Aufgabe bricht immer wieder ab",
                    message=(
                        f"„{(meta.get('title') or job.ref_id or '')[:90]}" + "\u201c wurde "
                        f"{_MAX_RESUMES}-mal nach einer Unterbrechung neu gestartet und "
                        f"kommt nicht durch. Ich starte sie nicht weiter — sieh sie dir an."
                    )[:240],
                    priority="high",
                    action_url=f"/tasks/{job.ref_id}" if job.ref_id else "/tasks",
                    meta={"reason": "resume_limit", "resume_count": resume_count},
                ))
                await db.commit()
                await delete_job(db, job.id)
                logger.warning(
                    "[Resume] Job %s nach %s Fortsetzungen gestoppt (Aufgabe %s)",
                    job.id, resume_count - 1, job.ref_id,
                )
                return

            # Retire a non-terminal original so it can't run alongside the replacement.
            if orig is not None and not is_terminal_task_status(orig.status):
                if orig.status == TaskStatus.QUEUED and orig.agent_id:
                    await router._remove_from_queue(orig.agent_id, orig.id)
                orig.status = TaskStatus.FAILED
                orig.error = "Superseded by auto-resume after container restart"
                orig.completed_at = datetime.now(timezone.utc)
                orig.notified = True
                await db.commit()

            try:
                new_task = await router.create_and_route_task(
                    title=meta.get("title") or f"[Resumed] {job.ref_id or job.id}",
                    prompt=prompt,
                    priority=int(meta.get("priority") or 5),
                    agent_id=meta.get("agent_id"),
                    model=meta.get("model"),
                    metadata={"resumed_from_task": job.ref_id, "resumed_from_job": job.id,
                              "resume_count": resume_count},
                )
            except UnknownAgentError as e:
                # Der Agent wurde geloescht, waehrend seine Aufgabe unterbrochen
                # war. Sie kann niemandem mehr gehoeren — den Auftrag verwerfen,
                # statt ihn bei jedem Start erneut zu versuchen.
                logger.warning("[Resume] Job %s verworfen: %s", job.id, e)
                await delete_job(db, job.id)
                return
            await delete_job(db, job.id)
            logger.info(
                f"[Resume] Re-enqueued interrupted job {job.id} (task {job.ref_id}) as new task {new_task.id}"
            )

    try:
        from app.db.session import async_session_factory as _sf_jobs
        from app.services.job_state import (
            recover_jobs_on_startup,
            register_resume_handler,
            relaunch_resumable_jobs,
        )

        register_resume_handler("agent_task", _resume_agent_task)

        async with _sf_jobs() as db:
            resumable, crashed = await recover_jobs_on_startup(db)
            if resumable:
                logger.info(f"[Startup] {len(resumable)} long job(s) resumable after restart")
                outcomes = await relaunch_resumable_jobs(resumable, schedule=asyncio.create_task)
                launched = sum(1 for _job, ok in outcomes if ok)
                logger.info(f"[Startup] Re-launched {launched}/{len(resumable)} resumable job(s)")
            for job in crashed:
                logger.warning(f"[Startup] Job {job.id} ({job.kind}) crashed across restart — no heartbeat")
                try:
                    await app.state.redis.publish(
                        "telegram:notification",
                        json.dumps({
                            "type": "error",
                            "title": "Job nach Neustart abgestürzt",
                            "message": f"Job '{job.kind}' ({job.id}) hat den Container-Neustart nicht überlebt (kein Heartbeat).",
                            "priority": "high",
                        }),
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Job-state recovery failed: {e}")

    # Start background task listener (completions + starts)
    completion_task = asyncio.create_task(_listen_task_events(app.state.redis))

    # Start chat completion persistence listener
    chat_persist_task = asyncio.create_task(_listen_chat_completions(app.state.redis))

    # Start task-step persistence listener (time-travel replay, issue #54)
    step_persist_task = asyncio.create_task(_persist_task_steps(app.state.redis))

    # Start third-party OAuth token refresh background task
    oauth_refresh_task = asyncio.create_task(_refresh_oauth_tokens(app.state.redis))

    # Start OAuth-protected MCP server token refresh background task (#488)
    mcp_oauth_refresh_task = asyncio.create_task(_refresh_mcp_oauth_tokens())

    # Start Claude Code OAuth token refresh background task
    claude_token_task = asyncio.create_task(_refresh_claude_token())

    # Start inter-agent message persistence listener
    message_persist_task = asyncio.create_task(_persist_agent_messages(app.state.redis))

    # Start scheduler service for recurring tasks
    from app.services.scheduler_service import SchedulerService

    scheduler = SchedulerService(app.state.redis, docker_service=app.state.docker)
    scheduler_task = asyncio.create_task(scheduler.run())

    # Start Sentinel service (Sentinel epic #588, skeleton per #590). Off by
    # default via settings.sentinel_enabled — see sentinel_service.py docstring:
    # _scan never triggers yet, so this has no observable effect even when on.
    sentinel_task = None
    if settings.sentinel_enabled:
        from app.services.sentinel_service import SentinelService

        sentinel = SentinelService(app.state.redis, app.state.docker)
        app.state.sentinel = sentinel
        sentinel_task = asyncio.create_task(sentinel.run())

    # Start skill catalog crawler (weekly GitHub crawl)
    from app.services.skill_crawler import SkillCrawlerService

    skill_crawler = SkillCrawlerService(app.state.redis)
    app.state.skill_crawler = skill_crawler
    skill_crawler_task = asyncio.create_task(skill_crawler.run())

    # On startup: import skills from all running agent containers into DB
    asyncio.create_task(_import_container_skills(app.state.docker))

    # Resume any meeting rooms that were running before restart
    from app.api.meeting_rooms import resume_running_rooms
    asyncio.create_task(resume_running_rooms(app.state.redis, docker=app.state.docker))

    # Start improvement engine (periodic rating analysis)
    from app.services.improvement_engine import ImprovementEngine

    improvement_engine = ImprovementEngine()
    improvement_task = asyncio.create_task(improvement_engine.run())

    # Start self-test service (daily health checks + self-improvement)
    from app.services.self_test_service import SelfTestService

    self_test = SelfTestService()
    self_test_task = asyncio.create_task(self_test.run())
    app.state.self_test = self_test

    # Start user lifecycle service (auto-stop agents of inactive users)
    from app.services.user_lifecycle import UserLifecycleService
    from app.db.session import async_session_factory as _sf_lc

    user_lifecycle = UserLifecycleService(_sf_lc, app.state.docker, app.state.redis)
    user_lifecycle_task = asyncio.create_task(user_lifecycle.run())
    app.state.user_lifecycle = user_lifecycle

    # Start disk monitor (workspace quota enforcement, every 5 min)
    from app.services.disk_monitor import DiskMonitorService
    from app.db.session import async_session_factory as _sf_disk

    disk_monitor = DiskMonitorService(_sf_disk, app.state.docker)
    disk_monitor_task = asyncio.create_task(disk_monitor.run())
    app.state.disk_monitor = disk_monitor

    # Start embedding backfill (for semantic memory search)
    from app.services.embedding_backfill import run_backfill_loop
    from app.db.session import async_session_factory as _sf_emb

    embedding_backfill_task = asyncio.create_task(run_backfill_loop(_sf_emb))

    # Start global Telegram bot if configured (for notifications)
    telegram_task = None
    if settings.telegram_bot_token:
        from app.telegram.bot import TelegramBot

        bot = TelegramBot()
        telegram_task = asyncio.create_task(bot.start())
        app.state.telegram_bot = bot

    # Start per-agent Telegram bots
    from app.telegram.bot_manager import TelegramBotManager
    from app.db.session import async_session_factory

    tg_manager = TelegramBotManager()
    app.state.telegram_bot_manager = tg_manager
    async with async_session_factory() as db:
        await tg_manager.load_all_from_db(db)

    yield

    # Cleanup
    completion_task.cancel()
    chat_persist_task.cancel()
    step_persist_task.cancel()
    oauth_refresh_task.cancel()
    mcp_oauth_refresh_task.cancel()
    claude_token_task.cancel()
    scheduler_task.cancel()
    if sentinel_task:
        sentinel.stop()
        sentinel_task.cancel()
    skill_crawler_task.cancel()
    improvement_task.cancel()
    self_test_task.cancel()
    user_lifecycle.stop()
    user_lifecycle_task.cancel()
    disk_monitor.stop()
    disk_monitor_task.cancel()
    embedding_backfill_task.cancel()
    if telegram_task:
        telegram_task.cancel()
        if hasattr(app.state, "telegram_bot"):
            await app.state.telegram_bot.stop()
    await tg_manager.stop_all()
    await app.state.redis.disconnect()


# --- App ---


app = FastAPI(
    title="AI Employee Orchestrator",
    description="Manages autonomous Claude Code agents in Docker containers",
    version="0.1.0",
    lifespan=lifespan,
)

# Security headers
app.add_middleware(SecurityHeadersMiddleware)

# API rate limiting (120 requests/minute per user or IP)
app.add_middleware(APIRateLimitMiddleware, max_requests=120, window_seconds=60)

# CORS - allow access from any origin so the app works from LAN, VPN, etc.
# In production, restrict via CORS_ALLOW_ORIGIN env var.
_cors_env = os.environ.get("CORS_ALLOW_ORIGIN", "").strip()
if _cors_env == "*" or not _cors_env:
    # Allow all origins (dev mode / LAN access)
    # NOTE: allow_origin_regex echoes the actual Origin header back instead of "*",
    # which is required when allow_credentials=True (browser CORS spec).
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=".*",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
else:
    _allowed_origins = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        _cors_env,
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )


# Delegation an einen Agenten, den es nicht (mehr) gibt: 400 mit der vollen
# Meldung. Sie ist an den AGENTEN gerichtet — er liest sie als Werkzeug-Antwort
# und kann seinen Auftrag im selben Zug korrigieren. Zentral registriert, damit
# JEDER Weg zur Auftragserstellung sie gleich behandelt (Werkzeug, Oberflaeche,
# Team-Ansicht, Zeitplan) statt jeder fuer sich.
@app.exception_handler(UnknownAgentError)
async def _unknown_agent_handler(request, exc: UnknownAgentError):  # noqa: ARG001
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(api_router, prefix="/api/v1")

# OAuth discovery documents (RFC 8414 / RFC 9728) MUST live at well-known ROOT
# paths so MCP clients (e.g. OpenWebUI) can discover the authorization server.
from app.api.oauth_as import wellknown_router as oauth_wellknown_router
app.include_router(oauth_wellknown_router)

# Computer-Use bridge WebSocket — mounted at root (not under /api/v1) so
# the bridge client can connect at ws://host/ws/computer-use/bridge
from app.api.computer_use import ws_router as cu_ws_router
app.include_router(cu_ws_router)


@app.get("/healthz")
@app.get("/health")
@app.get("/api/v1/health")
async def health_check(request: Request):
    """
    Enhanced health check that verifies database, Redis, and Docker connectivity.
    Returns HTTP 200 if all checks pass, 503 if any critical component is down.
    """
    from sqlalchemy import text as sa_text
    from app.db.session import async_session_factory

    checks: dict[str, dict] = {}
    overall_healthy = True

    # Database check
    try:
        async with async_session_factory() as db:
            await db.execute(sa_text("SELECT 1"))
        checks["database"] = {"status": "healthy"}
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}
        overall_healthy = False

    # Redis check
    try:
        redis: RedisService = request.app.state.redis
        if redis.client:
            await redis.client.ping()
            checks["redis"] = {"status": "healthy"}
        else:
            checks["redis"] = {"status": "unhealthy", "error": "not connected"}
            overall_healthy = False
    except Exception as e:
        checks["redis"] = {"status": "unhealthy", "error": str(e)}
        overall_healthy = False

    # Docker check
    try:
        docker: DockerService = request.app.state.docker
        containers = docker.list_agent_containers()
        checks["docker"] = {"status": "healthy", "agent_containers": len(containers)}
    except Exception as e:
        # Docker being unavailable is non-critical for the API itself
        checks["docker"] = {"status": "degraded", "error": str(e)}

    status_code = 200 if overall_healthy else 503
    response_body = {
        "status": "healthy" if overall_healthy else "unhealthy",
        "service": "orchestrator",
        "checks": checks,
    }

    return Response(
        content=json.dumps(response_body),
        status_code=status_code,
        media_type="application/json",
    )
