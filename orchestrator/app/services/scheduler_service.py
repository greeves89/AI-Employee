import asyncio
import logging
from datetime import datetime, timedelta, timezone

try:
    from croniter import croniter
    _CRONITER_AVAILABLE = True
except ImportError:
    _CRONITER_AVAILABLE = False

from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import PROACTIVE_PROMPT
from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.db.session import async_session_factory
from app.models.schedule import Schedule
from app.models.task import Task
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# GC runs every 60 seconds
_GC_INTERVAL_SECONDS = 60


class SchedulerService:
    """Background service that checks for due schedules and spawns tasks."""

    def __init__(self, redis: RedisService, docker_service=None):
        self.redis = redis
        self.docker = docker_service
        self._gc_counter = 0
        self._feeds_counter = 0

    async def run(self) -> None:
        """Main loop - checks every 30s. Runs schedules always, GC every 60s,
        knowledge-feeds every 5 minutes (feeds decide per-feed whether due)."""
        print("[Scheduler] Service started")
        from app.services.knowledge_feed_service import KnowledgeFeedService
        feed_service = KnowledgeFeedService(self.redis)

        while True:
            try:
                await self._check_due_schedules()
                self._gc_counter += 30
                if self._gc_counter >= _GC_INTERVAL_SECONDS:
                    self._gc_counter = 0
                    await self._gc_expired_tasks()
                self._feeds_counter += 30
                if self._feeds_counter >= 300:  # every 5 min
                    self._feeds_counter = 0
                    try:
                        summary = await feed_service.tick()
                        if summary.get("ran", 0) > 0:
                            print(
                                f"[Scheduler] KnowledgeFeeds: ran={summary['ran']} "
                                f"new={summary['new_items']} err={summary['errors']}"
                            )
                    except Exception as e:
                        print(f"[Scheduler] KnowledgeFeeds error: {e}")
            except Exception as e:
                print(f"[Scheduler] ERROR: {e}")
            await asyncio.sleep(30)

    async def _gc_expired_tasks(self) -> None:
        """Garbage-collect tasks whose evict_after timestamp has passed.

        Only tasks with:
          - evict_after <= now  (grace period expired)
          - retain == False     (UI is not holding them)
          - notified == True    (parent was informed)
        are eligible for deletion.
        """
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(Task).where(
                        and_(
                            Task.evict_after <= now,
                            Task.retain.is_(False),
                            Task.notified.is_(True),
                        )
                    )
                )
                expired = list(result.scalars().all())
                if not expired:
                    return
                from app.models.task_rating import TaskRating
                expired_ids = [t.id for t in expired]
                await db.execute(delete(TaskRating).where(TaskRating.task_id.in_(expired_ids)))
                for task in expired:
                    await db.delete(task)
                await db.commit()
                print(f"[Scheduler] GC: evicted {len(expired)} expired task(s)")
            except Exception as e:
                print(f"[Scheduler] GC error: {e}")

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
        is_cron = bool(schedule.cron_expression and _CRONITER_AVAILABLE)
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
                schedule.next_run_at = _calc_next_run(schedule, now)
                print(
                    f"[Scheduler] Proactive {schedule.name} skipped - "
                    f"agent busy (queue={queue_depth}, task={current_task!r})"
                )
                return

        # Meeting schedules: prompt starts with __meeting__:{json}
        if schedule.prompt.startswith("__meeting__:"):
            await self._execute_meeting_schedule(db, schedule)
            schedule.last_run_at = now
            schedule.total_runs += 1
            # One-shot: disable after firing (interval_seconds == 0)
            if not schedule.cron_expression and schedule.interval_seconds == 0:
                schedule.enabled = False
                schedule.next_run_at = now  # won't fire again since disabled
            else:
                schedule.next_run_at = _calc_next_run(schedule, now)
            print(f"[Scheduler] Meeting schedule {schedule.name} executed")
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
        schedule.next_run_at = _calc_next_run(schedule, now)

        print(
            f"[Scheduler] {schedule.name} triggered task {task.id}, "
            f"next run at {schedule.next_run_at.isoformat()}"
        )


    async def _execute_meeting_schedule(self, db: AsyncSession, schedule: Schedule) -> None:
        """Create and start a scheduled meeting room."""
        import json as _json
        import uuid as _uuid
        from app.models.meeting_room import MeetingRoom

        try:
            config = _json.loads(schedule.prompt[len("__meeting__:"):])
        except Exception as e:
            print(f"[Scheduler] Bad meeting config for {schedule.id}: {e}")
            return

        room = MeetingRoom(
            id=_uuid.uuid4().hex[:12],
            name=config.get("name", schedule.name),
            topic=config.get("topic", ""),
            agent_ids=config.get("agent_ids", []),
            max_rounds=config.get("max_rounds", 5),
            stages_config=config.get("stages_config"),
            use_moderator=config.get("use_moderator", True),
            created_by=config.get("created_by", "schedule"),
            messages=[{
                "role": "system",
                "agent_id": None,
                "content": config.get("initial_message", "Geplantes Meeting startet."),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        )
        db.add(room)
        await db.flush()  # get room.id before starting

        # Start the meeting loop
        from app.api.meeting_rooms import _run_meeting, _start_moderator_container, _running_rooms
        room.state = "running"

        mod_agent_id = None
        if room.use_moderator and self.docker:
            from app.config import settings as _settings
            mod_agent_id = await _start_moderator_container(room.id, self.docker, _settings.redis_url_internal)

        import asyncio
        task = asyncio.create_task(_run_meeting(room.id, self.redis, mod_agent_id=mod_agent_id, docker=self.docker))
        _running_rooms[room.id] = task
        print(f"[Scheduler] Started scheduled meeting {room.id}: {room.name}")


def _calc_next_run(schedule: "Schedule", now: datetime) -> datetime:
    """Return the next fire time for a schedule.

    If cron_expression is set and croniter is available, use it.
    Otherwise fall back to interval_seconds.
    """
    if schedule.cron_expression and _CRONITER_AVAILABLE:
        try:
            cron = croniter(schedule.cron_expression, now)
            return cron.get_next(datetime).replace(tzinfo=timezone.utc)
        except Exception as e:
            print(f"[Scheduler] Invalid cron expression '{schedule.cron_expression}': {e} — falling back to interval")
    return now + timedelta(seconds=max(schedule.interval_seconds, 60))
