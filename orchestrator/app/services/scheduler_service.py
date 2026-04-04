import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import PROACTIVE_PROMPT
from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.db.session import async_session_factory
from app.models.schedule import Schedule
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Background service that checks for due schedules and spawns tasks."""

    def __init__(self, redis: RedisService, docker_service=None):
        self.redis = redis
        self.docker = docker_service

    async def run(self) -> None:
        """Main loop - checks every 30 seconds for due schedules."""
        print("[Scheduler] Service started")
        while True:
            try:
                await self._check_due_schedules()
            except Exception as e:
                print(f"[Scheduler] ERROR: {e}")
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
            router = TaskRouter(db, self.redis, lb, docker_service=self.docker)

            for schedule in schedules:
                try:
                    await self._execute_schedule(db, router, schedule, now)
                except Exception as e:
                    print(f"[Scheduler] Failed to execute schedule {schedule.id}: {e}")

            await db.commit()

    async def _execute_schedule(
        self,
        db: AsyncSession,
        router: TaskRouter,
        schedule: Schedule,
        now: datetime,
    ) -> None:
        """Create a task from a schedule and advance next_run_at."""
        # Skip proactive schedules if the agent is busy with a TASK (not chat)
        if schedule.name.startswith("[Proactive]") and schedule.agent_id:
            queue_depth = await self.redis.get_queue_depth(schedule.agent_id)
            status = await self.redis.get_agent_status(schedule.agent_id)
            current_task = status.get("current_task", "")
            # Chat sessions (current_task starts with "chat:") don't block proactive tasks
            # because chat and task consumers run concurrently in the agent
            is_busy_with_task = queue_depth > 0 or (
                current_task and not current_task.startswith("chat:")
            )
            if is_busy_with_task:
                schedule.next_run_at = now + timedelta(seconds=schedule.interval_seconds)
                print(
                    f"[Scheduler] Proactive {schedule.name} skipped - "
                    f"agent busy (queue={queue_depth}, task={current_task!r})"
                )
                return

        # For proactive schedules: always use the latest PROACTIVE_PROMPT from code
        # so prompt improvements apply immediately to all agents without DB migration
        is_proactive = schedule.name.startswith("[Proactive]")
        prompt = PROACTIVE_PROMPT if is_proactive else schedule.prompt

        task = await router.create_and_route_task(
            title=f"[Scheduled] {schedule.name}",
            prompt=prompt,
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

        print(
            f"[Scheduler] {schedule.name} triggered task {task.id}, "
            f"next run at {schedule.next_run_at.isoformat()}"
        )
