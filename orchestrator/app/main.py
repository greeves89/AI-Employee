import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.api.ws import init_stream_manager
from app.config import settings
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


# --- Security Headers Middleware ---


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # CSP - allow self + inline styles (Tailwind) + wss for websockets
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self' data:; "
            "frame-ancestors 'none'"
        )
        return response


# --- API Rate Limiting Middleware ---


class APIRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user / per-IP rate limiting backed by Redis.

    Uses Redis INCR + EXPIRE for distributed, restart-safe counters.
    Falls back to in-memory tracking if Redis is unavailable.
    """

    def __init__(self, app, max_requests: int = 120, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        # In-memory fallback (only used if Redis is unreachable)
        self._fallback: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        import time

        # Skip rate limiting for health checks and WebSocket upgrades
        path = request.url.path
        if path in ("/health", "/healthz") or request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

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
            try:
                current = await redis_client.incr(redis_key)
                if current == 1:
                    await redis_client.expire(redis_key, self.window)
                if current > self.max_requests:
                    ttl = await redis_client.ttl(redis_key)
                    logger.warning(f"Rate limit exceeded for {key} ({current}/{self.max_requests})")
                    return Response(
                        content='{"detail":"Rate limit exceeded. Try again later."}',
                        status_code=429,
                        media_type="application/json",
                        headers={"Retry-After": str(max(ttl, 1))},
                    )
                return await call_next(request)
            except Exception:
                pass  # Redis unavailable — fall through to in-memory

        # In-memory fallback
        now = time.time()
        if key not in self._fallback:
            self._fallback[key] = []
        self._fallback[key] = [t for t in self._fallback[key] if now - t < self.window]

        if len(self._fallback[key]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for {key} (in-memory fallback)")
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(self.window)},
            )

        self._fallback[key].append(now)
        return await call_next(request)


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


async def _refresh_claude_token() -> None:
    """Background task that manages the Claude OAuth token lifecycle.

    - Reads token from host-auth/token.json (synced from macOS Keychain by launchd)
    - Only refreshes via Anthropic OAuth when token is actually expired
    - Checks file for changes every 2 min, but only calls Anthropic when needed
    - FORCED refresh daily at 01:00 UTC (03:00 German time) to prevent auth failures
    """
    from app.services.claude_token_service import ClaudeTokenService

    service = ClaudeTokenService()

    # Load initial token from Keychain file (or fallback to env/DB)
    service.write_initial_token()

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
                from app.services.apns_service import push_to_user
                from sqlalchemy import select as sel
                from sqlalchemy.exc import IntegrityError

                async with async_session_factory() as db:
                    # Check if assistant response already persisted (by WS handler)
                    existing = await db.scalar(
                        sel(ChatMessage).where(
                            ChatMessage.agent_id == agent_id,
                            ChatMessage.message_id == message_id,
                            ChatMessage.role == "assistant",
                        )
                    )
                    if existing:
                        continue  # Already saved by WebSocket handler

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

                    db.add(ChatMessage(
                        agent_id=agent_id,
                        session_id=session_id,
                        message_id=message_id,
                        role="assistant",
                        content=content,
                        tool_calls=tool_calls,
                        meta=meta,
                    ))
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

    # Mirror WARNING+ logs (redacted) to /shared/platform-errors.log so agents can
    # read platform errors from the shared volume and help fix the platform.
    from app.core.platform_error_log import setup_platform_error_log
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
        logger.info("chat_sessions table ensured")
    except Exception as e:
        logger.warning(f"Could not ensure chat_sessions table: {e}")

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
                    tmpl = AgentTemplate(is_builtin=True, **tmpl_data)
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
    token_svc.write_initial_token()
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
    except Exception as e:
        logger.warning(f"Agent startup recovery failed: {e}")

    # Start background task listener (completions + starts)
    completion_task = asyncio.create_task(_listen_task_events(app.state.redis))

    # Start chat completion persistence listener
    chat_persist_task = asyncio.create_task(_listen_chat_completions(app.state.redis))

    # Start task-step persistence listener (time-travel replay, issue #54)
    step_persist_task = asyncio.create_task(_persist_task_steps(app.state.redis))

    # Start third-party OAuth token refresh background task
    oauth_refresh_task = asyncio.create_task(_refresh_oauth_tokens(app.state.redis))

    # Start Claude Code OAuth token refresh background task
    claude_token_task = asyncio.create_task(_refresh_claude_token())

    # Start inter-agent message persistence listener
    message_persist_task = asyncio.create_task(_persist_agent_messages(app.state.redis))

    # Start scheduler service for recurring tasks
    from app.services.scheduler_service import SchedulerService

    scheduler = SchedulerService(app.state.redis, docker_service=app.state.docker)
    scheduler_task = asyncio.create_task(scheduler.run())

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
    claude_token_task.cancel()
    scheduler_task.cancel()
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
