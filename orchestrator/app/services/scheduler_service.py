import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.db.session import async_session_factory
from app.models.schedule import Schedule
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Background service that checks for due schedules and spawns tasks."""

    def __init__(self, redis: RedisService):
        self.redis = redis

    async def run(self) -> None:
        """Main loop - checks every 30 seconds for due schedules."""
        logger.info("Scheduler service started")
        while True:
            try:
                await self._check_due_schedules()
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
            await asyncio.sleep(30)

    async def _check_due_schedules(self) -> None:
        now = datetime.now(timezone.utc)

        async with async_session_factory() as db:
            result = await db.execute(
                select(Schedule).where(
                    Schedule.enabled == True,  # noqa: E712
                    Schedule.next_run_at <= now,
                )
            )
            schedules = list(result.scalars().all())

            if not schedules:
                return

            lb = LoadBalancer(self.redis)
            router = TaskRouter(db, self.redis, lb)

            for schedule in schedules:
                try:
                    await self._execute_schedule(db, router, schedule, now)
                except Exception as e:
                    logger.error(f"Failed to execute schedule {schedule.id}: {e}")

            await db.commit()

    async def _execute_schedule(
        self,
        db: AsyncSession,
        router: TaskRouter,
        schedule: Schedule,
        now: datetime,
    ) -> None:
        """Create a task from a schedule and advance next_run_at."""
        # Skip proactive schedules if the agent is already busy
        if schedule.name.startswith("[Proactive]") and schedule.agent_id:
            queue_depth = await self.redis.get_queue_depth(schedule.agent_id)
            status = await self.redis.get_agent_status(schedule.agent_id)
            if queue_depth > 0 or status.get("current_task"):
                # Agent is busy - postpone proactive check
                schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
                logger.debug(
                    f"Proactive schedule {schedule.id} skipped - agent {schedule.agent_id} is busy"
                )
                return

        task = await router.create_and_route_task(
            title=f"[Scheduled] {schedule.name}",
            prompt=schedule.prompt,
            priority=schedule.priority,
            agent_id=schedule.agent_id,
            model=schedule.model,
        )

        # Tag the task with schedule_id for completion tracking
        task.metadata_ = {**(task.metadata_ or {}), "schedule_id": schedule.id}

        # Advance schedule
        schedule.last_run_at = now
        schedule.total_runs += 1
        schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)

        logger.info(
            f"Schedule {schedule.id} ({schedule.name}) triggered task {task.id}, "
            f"next run at {schedule.next_run_at.isoformat()}"
        )
