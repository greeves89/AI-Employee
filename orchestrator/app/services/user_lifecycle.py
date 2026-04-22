"""User Lifecycle Service — auto start/stop agents based on user activity.

Rules:
- If a user is inactive > INACTIVITY_MINUTES and has no running tasks,
  stop all of that user's agent containers.
- When user logs in / sends a message / task is dispatched to an agent,
  that agent (or all of the user's agents) is woken up.
- Waking is async (non-blocking); caller can await readiness if needed.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentState
from app.models.user import User

logger = logging.getLogger(__name__)

# Default: stop a user's agents after 30 minutes of inactivity.
# Overridable via platform_settings key "agent_idle_timeout_minutes":
#   0   = never stop (persistent mode, like OpenClaw)
#   30  = stop after 30 min (resource-friendly, default)
#   1440 = stop after 24 h
DEFAULT_INACTIVITY_MINUTES = 30
# How often the background loop runs
CHECK_INTERVAL_SECONDS = 60


async def _get_timeout_minutes(db: AsyncSession) -> int:
    """Read the configured idle-timeout from platform_settings (cached)."""
    try:
        from app.services.settings_service import SettingsService
        svc = SettingsService(db)
        value = await svc.get("agent_idle_timeout_minutes")
        if value is not None and value != "":
            return int(value)
    except Exception:
        pass
    return DEFAULT_INACTIVITY_MINUTES


class UserLifecycleService:
    """Background service that auto-stops agents of inactive users."""

    def __init__(self, db_factory, docker_service, redis_service):
        self.db_factory = db_factory
        self.docker = docker_service
        self.redis = redis_service
        self._running = False

    async def run(self) -> None:
        """Main loop: every minute, check for inactive users and stop their agents."""
        self._running = True
        logger.info("[UserLifecycle] Started")
        while self._running:
            try:
                await self._sweep()
            except Exception as e:
                logger.error(f"[UserLifecycle] Sweep error: {e}")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    async def _sweep(self) -> None:
        """One sweep: find inactive users and stop their idle agents."""
        async with self.db_factory() as db:
            global_timeout = await _get_timeout_minutes(db)
            now = datetime.now(timezone.utc)

            # Load all running agents and their users in one pass
            result = await db.execute(
                select(Agent).where(Agent.state.in_([AgentState.RUNNING, AgentState.IDLE]))
            )
            agents = list(result.scalars().all())
            if not agents:
                return

            # Resolve unique user IDs → last_active_at
            user_ids = {a.user_id for a in agents if a.user_id}
            if not user_ids:
                return
            users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
            user_map = {u.id: u for u in users_result.scalars().all()}

            total_stopped = 0
            for agent in agents:
                user = user_map.get(agent.user_id)
                if not user:
                    continue

                # Per-agent timeout overrides global; 0 = never stop this agent
                per_agent = agent.config.get("idle_timeout_minutes") if agent.config else None
                if per_agent is not None:
                    timeout = int(per_agent)
                else:
                    timeout = global_timeout

                if timeout <= 0:
                    continue  # persistent mode for this agent

                threshold = now - timedelta(minutes=timeout)
                last_active = user.last_active_at
                if last_active is not None and last_active >= threshold:
                    continue  # user was active recently enough for this agent's timeout

                # Skip agents with queued/running tasks
                queue_depth = await self.redis.get_queue_depth(agent.id)
                status = await self.redis.get_agent_status(agent.id)
                if queue_depth > 0 or status.get("state") == "working":
                    continue

                try:
                    if agent.container_id:
                        self.docker.stop_container(agent.container_id)
                    agent.state = AgentState.STOPPED
                    total_stopped += 1
                    logger.info(
                        f"[UserLifecycle] Stopped agent {agent.name} ({agent.id}) "
                        f"after {timeout}min inactivity (user {user.email})"
                    )
                except Exception as e:
                    logger.warning(f"[UserLifecycle] Could not stop {agent.id}: {e}")

            if total_stopped > 0:
                await db.commit()
                logger.info(f"[UserLifecycle] Auto-stopped {total_stopped} agents of inactive users")

    async def _stop_user_agents(self, db: AsyncSession, user: User) -> int:
        """Stop all of a user's running agents that have no pending work.

        Returns the number of agents stopped.
        """
        result = await db.execute(
            select(Agent).where(Agent.user_id == user.id).where(
                Agent.state.in_([AgentState.RUNNING, AgentState.IDLE])
            )
        )
        agents = list(result.scalars().all())
        stopped = 0
        for agent in agents:
            # Skip agents with queued/running tasks
            queue_depth = await self.redis.get_queue_depth(agent.id)
            status = await self.redis.get_agent_status(agent.id)
            if queue_depth > 0 or status.get("state") == "working":
                continue
            try:
                if agent.container_id:
                    self.docker.stop_container(agent.container_id)
                agent.state = AgentState.STOPPED
                stopped += 1
                logger.info(f"[UserLifecycle] Stopped agent {agent.name} ({agent.id}) of user {user.email}")
            except Exception as e:
                logger.warning(f"[UserLifecycle] Could not stop {agent.id}: {e}")
        if stopped > 0:
            await db.commit()
        return stopped


# ─── Wake helpers (called synchronously from request handlers) ─────────


async def wake_user_agents(db: AsyncSession, docker_service, user_id: str) -> list[str]:
    """Start all stopped containers for a user's agents. Returns list of agent IDs woken.

    Non-blocking: starts containers but does NOT wait for them to be healthy.
    Callers that need the agent ready should use wake_agent() with wait=True.
    """
    result = await db.execute(
        select(Agent).where(Agent.user_id == user_id).where(
            Agent.state.in_([AgentState.STOPPED, AgentState.ERROR])
        )
    )
    agents = list(result.scalars().all())
    woken: list[str] = []
    for agent in agents:
        try:
            if agent.container_id:
                docker_service.start_container(agent.container_id)
                agent.state = AgentState.RUNNING
                woken.append(agent.id)
        except Exception as e:
            logger.warning(f"[UserLifecycle] Could not wake {agent.id}: {e}")
    if woken:
        await db.commit()
        logger.info(f"[UserLifecycle] Woke {len(woken)} agents for user {user_id}")
    return woken


async def wake_agent(db: AsyncSession, docker_service, agent_id: str, wait: bool = False, timeout: int = 30) -> bool:
    """Ensure a single agent is running. Returns True if the agent is ready.

    If the stored container_id no longer exists (e.g. removed by docker rm),
    falls back to a full agent restart via AgentManager to recreate the container.
    """
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if not agent:
        return False

    if agent.state in (AgentState.RUNNING, AgentState.WORKING, AgentState.IDLE):
        # Double-check the container actually exists
        if agent.container_id:
            status = docker_service.get_container_status(agent.container_id)
            if status in ("running", "created"):
                return True

    # Try to start the existing container
    if agent.container_id:
        status = docker_service.get_container_status(agent.container_id)
        if status == "running":
            agent.state = AgentState.RUNNING
            await db.commit()
            return True
        if status in ("exited", "created", "paused"):
            try:
                docker_service.start_container(agent.container_id)
                agent.state = AgentState.RUNNING
                await db.commit()
                logger.info(f"[UserLifecycle] Woke agent {agent.name} ({agent.id})")
                if wait:
                    for _ in range(timeout):
                        await asyncio.sleep(1)
                        if docker_service.get_container_status(agent.container_id) == "running":
                            return True
                return True
            except Exception as e:
                logger.warning(f"[UserLifecycle] start_container failed for {agent_id}: {e}")

    # Container missing or start failed — recreate via AgentManager
    logger.info(f"[UserLifecycle] Container gone for {agent_id}, recreating via AgentManager")
    try:
        from app.core.agent_manager import AgentManager
        from app.services.redis_service import RedisService
        from app.config import settings
        redis_service = RedisService(settings.redis_url)
        manager = AgentManager(db, docker_service, redis_service)
        await manager.restart_agent(agent_id)
        if wait:
            for _ in range(timeout):
                await asyncio.sleep(1)
                agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
                if agent and agent.container_id:
                    status = docker_service.get_container_status(agent.container_id)
                    if status == "running":
                        return True
        return True
    except Exception as e:
        logger.warning(f"[UserLifecycle] Could not recreate agent {agent_id}: {e}")
        return False
