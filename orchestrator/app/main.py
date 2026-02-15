import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.ws import init_stream_manager
from app.config import settings
from app.db.session import engine
from app.models.base import Base
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService


async def _refresh_oauth_tokens(redis: RedisService) -> None:
    """Background task that refreshes OAuth tokens before they expire."""
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


async def _listen_task_completions(redis: RedisService) -> None:
    """Background task that listens for task completion events from agents."""
    pubsub = await redis.subscribe("task:completions")
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

                # Import here to avoid circular imports
                from app.core.load_balancer import LoadBalancer
                from app.core.task_router import TaskRouter
                from app.db.session import async_session_factory

                async with async_session_factory() as db:
                    lb = LoadBalancer(redis)
                    router = TaskRouter(db, redis, lb)
                    await router.handle_task_completion(data)
        except Exception:
            await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables (ignore if enums/tables already exist)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        # Tables/enums already exist from a previous run - that's fine
        pass

    # Initialize services
    app.state.redis = RedisService(settings.redis_url)
    await app.state.redis.connect()
    app.state.docker = DockerService()

    # Initialize stream manager for WebSocket
    init_stream_manager(app.state.redis, app.state.docker)

    # Start background task listener
    completion_task = asyncio.create_task(_listen_task_completions(app.state.redis))

    # Start OAuth token refresh background task
    oauth_refresh_task = asyncio.create_task(_refresh_oauth_tokens(app.state.redis))

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
    oauth_refresh_task.cancel()
    scheduler_task.cancel()
    if telegram_task:
        telegram_task.cancel()
        if hasattr(app.state, "telegram_bot"):
            await app.state.telegram_bot.stop()
    await app.state.redis.disconnect()


app = FastAPI(
    title="AI Employee Orchestrator",
    description="Manages autonomous Claude Code agents in Docker containers",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "orchestrator"}
