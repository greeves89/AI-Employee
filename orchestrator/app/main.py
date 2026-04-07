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
    pubsub = await redis.subscribe("task:completions")
    # Also subscribe to task:started channel
    if redis.client:
        await pubsub.subscribe("task:started")

    while True:
        try:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message and message["type"] == "message":
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
        except Exception:
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

                if not agent_id or not message_id:
                    continue

                from app.db.session import async_session_factory
                from app.models.chat_message import ChatMessage
                from sqlalchemy import select as sel

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

                    # Look up session_id from the user message
                    user_msg = await db.scalar(
                        sel(ChatMessage).where(
                            ChatMessage.agent_id == agent_id,
                            ChatMessage.message_id == message_id,
                            ChatMessage.role == "user",
                        )
                    )
                    if not user_msg:
                        print(f"[ChatPersist] No user message found for {message_id}, skipping")
                        continue

                    session_id = user_msg.session_id
                    content = str(event_data.get("text", ""))
                    tool_calls = event_data.get("tool_calls")
                    meta = {
                        "cost_usd": event_data.get("cost_usd"),
                        "duration_ms": event_data.get("duration_ms"),
                        "num_turns": event_data.get("num_turns"),
                    }

                    db.add(ChatMessage(
                        agent_id=agent_id,
                        session_id=session_id,
                        message_id=message_id,
                        role="assistant",
                        content=content,
                        tool_calls=tool_calls,
                        meta=meta,
                    ))
                    await db.commit()
                    print(
                        f"[ChatPersist] Saved response for {message_id} "
                        f"(agent={agent_id}, session={session_id})"
                    )
        except Exception as e:
            print(f"[ChatPersist] Error: {e}")
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
                        from_agent_id=data.get("from_agent_id", ""),
                        from_agent_name=data.get("from_name", ""),
                        to_agent_id=data.get("to_agent_id", ""),
                        text=data.get("text", ""),
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

    from app.models import Base  # noqa: F401

    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    logger.info("Tables created from SQLAlchemy models")

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


# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate config on startup
    _validate_config()

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
                        "integrations", "knowledge_template", "icon", "category", "model",
                    ):
                        source_val = tmpl_data.get(field)
                        if source_val is not None and getattr(existing, field) != source_val:
                            setattr(existing, field, source_val)
            await db.commit()
        logger.info(f"Seeded/synced {len(BUILTIN_TEMPLATES)} builtin agent templates")
    except Exception as e:
        logger.warning(f"Failed to seed templates: {e}")

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

    # Initialize services
    app.state.redis = RedisService(settings.redis_url)
    await app.state.redis.connect()
    app.state.docker = DockerService()

    # Initialize stream manager for WebSocket
    init_stream_manager(app.state.redis, app.state.docker)

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

    # Start background task listener (completions + starts)
    completion_task = asyncio.create_task(_listen_task_events(app.state.redis))

    # Start chat completion persistence listener
    chat_persist_task = asyncio.create_task(_listen_chat_completions(app.state.redis))

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

    # Start skill catalog crawler (daily GitHub crawl)
    from app.services.skill_crawler import SkillCrawlerService

    skill_crawler = SkillCrawlerService(app.state.redis)
    app.state.skill_crawler = skill_crawler
    skill_crawler_task = asyncio.create_task(skill_crawler.run())

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
    oauth_refresh_task.cancel()
    claude_token_task.cancel()
    scheduler_task.cancel()
    skill_crawler_task.cancel()
    improvement_task.cancel()
    self_test_task.cancel()
    user_lifecycle.stop()
    user_lifecycle_task.cancel()
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


@app.get("/healthz")
@app.get("/health")
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
