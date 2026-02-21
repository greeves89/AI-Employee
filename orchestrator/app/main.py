import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

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
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self' data:; "
            "frame-ancestors 'none'"
        )
        return response


# --- API Rate Limiting Middleware ---


class APIRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user / per-IP rate limiting for API endpoints."""

    def __init__(self, app, max_requests: int = 120, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        import time

        # Skip rate limiting for health checks and WebSocket upgrades
        path = request.url.path
        if path == "/health" or request.headers.get("upgrade", "").lower() == "websocket":
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

        now = time.time()
        # Clean old entries
        if key not in self._requests:
            self._requests[key] = []
        self._requests[key] = [t for t in self._requests[key] if now - t < self.window]

        if len(self._requests[key]) >= self.max_requests:
            logger.warning(f"Rate limit exceeded for {key}")
            return Response(
                content='{"detail":"Rate limit exceeded. Try again later."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(self.window)},
            )

        self._requests[key].append(now)
        return await call_next(request)


# --- Config Validation ---


def _validate_config() -> None:
    """Validate critical security settings at startup."""
    warnings = []

    if not settings.encryption_key:
        warnings.append("ENCRYPTION_KEY is not set - OAuth token encryption will fail!")

    if settings.api_secret_key == "change-me-in-production":
        warnings.append(
            "API_SECRET_KEY is still the default value! "
            "Set a strong random key for JWT signing and agent auth."
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
    """Background task that refreshes the Claude Code OAuth access token every 2 hours.

    Access tokens expire after ~3-8 hours. We refresh proactively every 2h.
    Refresh tokens are single-use — each refresh returns a new pair.
    """
    from app.services.claude_token_service import ClaudeTokenService

    service = ClaudeTokenService()

    while True:
        try:
            if settings.claude_code_oauth_refresh_token:
                success = await service.refresh_access_token()
                if not success and service.consecutive_failures >= 3:
                    logger.error(
                        "Claude token refresh failed 3 times in a row - "
                        "the refresh token may be invalid. "
                        "Re-run 'claude login' and update the tokens."
                    )
        except Exception as e:
            logger.error(f"Claude token refresh task error: {e}")

        await asyncio.sleep(7200)  # Every 2 hours


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


# --- Lifespan ---


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate config on startup
    _validate_config()

    # Run Alembic migrations to create/update tables
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
            logger.error(f"Alembic migration failed: {result.stderr}")
            raise RuntimeError(f"Database migration failed: {result.stderr}")
        logger.info("Database migrations applied successfully")
    except subprocess.TimeoutExpired:
        logger.error("Alembic migration timed out")
        raise

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

    # Initial token refresh if we have a refresh token (gets a fresh access token)
    from app.services.claude_token_service import ClaudeTokenService

    token_svc = ClaudeTokenService()
    if settings.claude_code_oauth_refresh_token:
        try:
            success = await token_svc.refresh_access_token()
            if success:
                logger.info("Initial Claude token refresh successful - access token is fresh")
            else:
                logger.warning("Initial Claude token refresh failed - using existing access token")
                # Still write whatever token we have to the shared volume
                token_svc.write_initial_token()
        except Exception as e:
            logger.warning(f"Initial Claude token refresh failed: {e}")
            token_svc.write_initial_token()
    elif settings.claude_code_oauth_token:
        # No refresh token, but we have an access token - write it to shared volume
        token_svc.write_initial_token()

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

    # Start scheduler service for recurring tasks
    from app.services.scheduler_service import SchedulerService

    scheduler = SchedulerService(app.state.redis)
    scheduler_task = asyncio.create_task(scheduler.run())

    # Start Telegram bot if configured
    telegram_task = None
    if settings.telegram_bot_token:
        from app.telegram.bot import TelegramBot

        bot = TelegramBot()
        telegram_task = asyncio.create_task(bot.start())
        app.state.telegram_bot = bot

    yield

    # Cleanup
    completion_task.cancel()
    chat_persist_task.cancel()
    oauth_refresh_task.cancel()
    claude_token_task.cancel()
    scheduler_task.cancel()
    if telegram_task:
        telegram_task.cancel()
        if hasattr(app.state, "telegram_bot"):
            await app.state.telegram_bot.stop()
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


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "orchestrator"}
