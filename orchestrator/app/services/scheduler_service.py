import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
# "Dreaming": periodic adaptive user-profile refresh from accumulated memories
# (heuristic, no LLM cost). Gated by the dreaming_enabled setting (default off).
_DREAMING_INTERVAL_SECONDS = 3600


class SchedulerService:
    """Background service that checks for due schedules and spawns tasks."""

    def __init__(self, redis: RedisService, docker_service=None):
        self.redis = redis
        self.docker = docker_service
        self._gc_counter = 0
        self._feeds_counter = 0
        self._idle_stop_counter = 0
        self._failure_watchdog_last_run: datetime | None = None
        self._dreaming_counter = 0
        # Per-schedule drift value at which we last alerted; prevents hourly spam
        # for a stuck schedule — only re-alerts when drift increases.
        self._watchdog_alerted: dict[str, int] = {}

    async def run(self) -> None:
        """Main loop - checks every 30s. Runs schedules always, GC every 60s,
        knowledge-feeds every 5 minutes, trend scan every 24h."""
        print("[Scheduler] Service started")
        from app.services.knowledge_feed_service import KnowledgeFeedService
        from app.services.trend_service import TrendService
        from app.config import settings as _settings
        feed_service = KnowledgeFeedService(self.redis)
        trend_service = TrendService(self.redis, github_token=_settings.github_token)

        while True:
            try:
                await self._check_due_schedules()
                try:
                    started = await self._start_due_followups()
                    if started:
                        print(f"[Scheduler] Auto-started {started} follow-up meeting(s)")
                except Exception as e:
                    print(f"[Scheduler] Follow-up auto-start error: {e}")
                self._gc_counter += 30
                if self._gc_counter >= _GC_INTERVAL_SECONDS:
                    self._gc_counter = 0
                    await self._gc_expired_tasks()
                self._idle_stop_counter += 30
                if self._idle_stop_counter >= 300:  # every 5 min
                    self._idle_stop_counter = 0
                    try:
                        n = await self._stop_idle_agents()
                        if n > 0:
                            print(f"[Scheduler] IdleStop: stopped {n} idle agent(s)")
                    except Exception as e:
                        print(f"[Scheduler] IdleStop error: {e}")
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
                # Missed-run watchdog: catches schedules whose task never reported
                # a terminal status (silent drops). Self-throttles to once per hour.
                try:
                    await self._tick_failure_watchdog()
                except Exception as e:
                    print(f"[Scheduler] FailureWatchdog error: {e}")
                # Trend scan: runs daily (TrendService.tick() self-throttles)
                try:
                    result = await trend_service.tick()
                    if not result.get("skipped") and result.get("generated", 0) > 0:
                        print(
                            f"[Scheduler] TrendScan: scanned={result['scanned']} "
                            f"new={result['new']} generated={result['generated']} err={result['errors']}"
                        )
                except Exception as e:
                    print(f"[Scheduler] TrendScan error: {e}")
                # "Dreaming": refresh adaptive user profiles from memories (gated)
                self._dreaming_counter += 30
                if self._dreaming_counter >= _DREAMING_INTERVAL_SECONDS:
                    self._dreaming_counter = 0
                    try:
                        n = await self._run_dreaming()
                        if n > 0:
                            print(f"[Scheduler] Dreaming: refreshed {n} user profile(s)")
                    except Exception as e:
                        print(f"[Scheduler] Dreaming error: {e}")
            except Exception as e:
                print(f"[Scheduler] ERROR: {e}")
            await asyncio.sleep(30)

    async def _start_due_followups(self) -> int:
        """Auto-start idle follow-up meeting rooms — EVENT-BASED: start once the agents
        have FINISHED their assigned tasks from the parent meeting (the agents reliably
        complete tasks; they don't always tick the TODOs, so we key on task completion),
        or, as a safety net, once scheduled_for (the cap) is reached."""
        from datetime import datetime, timezone
        from sqlalchemy import select, and_, or_
        from app.db.session import async_session_factory
        from app.models.meeting_room import MeetingRoom
        from app.models.task import Task, TaskStatus
        from app.api.meeting_rooms import _run_meeting, _start_moderator_container, _running_rooms

        now = datetime.now(timezone.utc)
        started = 0
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(MeetingRoom).where(and_(
                    MeetingRoom.state == "idle",
                    MeetingRoom.is_active == True,
                    or_(MeetingRoom.parent_room_id.isnot(None), MeetingRoom.scheduled_for.isnot(None)),
                ))
            )).scalars().all()
            for room in rows:
                if room.id in _running_rooms:
                    continue
                cap_due = room.scheduled_for is not None and room.scheduled_for <= now
                tasks_done = False
                if room.parent_room_id:
                    statuses = (await db.execute(
                        select(Task.status).where(Task.metadata_.op("->>")("room_id") == room.parent_room_id)
                    )).scalars().all()
                    # Ready once every assigned meeting task has reached a terminal state
                    # (COMPLETED/FAILED) — i.e. the agents are done working.
                    if statuses:
                        tasks_done = all(s not in (TaskStatus.PENDING, TaskStatus.RUNNING) for s in statuses)
                if not (cap_due or tasks_done):
                    continue
                room.state = "running"
                room.current_turn = 0
                room.scheduled_for = None
                room.parent_room_id = None  # consume so it fires once
                await db.commit()
                mod_agent_id = None
                if room.use_moderator and self.docker:
                    from app.config import settings as _settings
                    mod_agent_id = await _start_moderator_container(room.id, self.docker, _settings.redis_url_internal)
                task = asyncio.create_task(_run_meeting(room.id, self.redis, mod_agent_id=mod_agent_id, docker=self.docker))
                _running_rooms[room.id] = task
                started += 1
                reason = "alle Aufgaben erledigt" if tasks_done else "Cap erreicht"
                print(f"[Scheduler] Auto-started follow-up {room.id} ({reason}): {room.name}")
        return started

    async def _run_dreaming(self) -> int:
        """'Dreaming': rebuild each active user's adaptive profile from their
        accumulated memories (heuristic, no LLM cost). Gated by ``dreaming_enabled``
        (default off). Per-user failures are isolated — never break the loop."""
        from app.services.settings_service import SettingsService
        from app.services.profile_extractor import extract_profile
        from app.models.agent import Agent
        async with async_session_factory() as db:
            enabled = (await SettingsService(db).get("dreaming_enabled")) or ""
            if enabled.lower() not in ("true", "1", "yes"):
                return 0
            rows = await db.execute(
                select(Agent.user_id).where(Agent.user_id.isnot(None)).distinct()
            )
            user_ids = [u for u in rows.scalars().all() if u]
            n = 0
            for uid in user_ids:
                try:
                    await extract_profile(db, uid)
                    n += 1
                except Exception:
                    logger.warning("Dreaming: profile extract failed for user %s", uid, exc_info=True)
            await db.commit()
            return n

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
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    print(f"[Scheduler] Failed to execute schedule {schedule.id}: {e}")

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
        # so prompt improvements apply immediately to all agents without DB migration.
        # Per-agent additions (admin/user editable in the UI) are appended to the
        # code base — stored as data in agent.config, never duplicating the base.
        is_proactive = schedule.name.startswith("[Proactive]")
        if is_proactive:
            prompt = PROACTIVE_PROMPT
            extra = await self._proactive_custom_instructions(db, schedule.agent_id)
            if extra:
                prompt = (
                    prompt
                    + "\n\n## Zusätzliche Anweisungen (vom Nutzer)\n"
                    + "Diese ergänzen die Schritte oben — befolge sie zusätzlich, "
                    + "ohne die Basisregeln zu verletzen.\n\n"
                    + extra
                )
        else:
            prompt = schedule.prompt

        task = await router.create_and_route_task(
            title=f"[Scheduled] {schedule.name}",
            prompt=prompt,
            priority=schedule.priority,
            agent_id=schedule.agent_id,
            model=schedule.model,
            metadata={"schedule_id": schedule.id},
        )

        # Advance schedule
        schedule.last_run_at = now
        schedule.total_runs += 1
        schedule.next_run_at = _calc_next_run(schedule, now)

        print(
            f"[Scheduler] {schedule.name} triggered task {task.id}, "
            f"next run at {schedule.next_run_at.isoformat()}"
        )

    async def _proactive_custom_instructions(
        self, db: AsyncSession, agent_id: str | None
    ) -> str:
        """Per-agent proactive additions from agent.config['proactive']['custom_instructions'].

        Appended to the code-level PROACTIVE_PROMPT at fire time so the base stays
        centralized in code (one source of truth) while each agent can carry its own
        extra instructions as plain data.
        """
        if not agent_id:
            return ""
        from sqlalchemy import select
        from app.models.agent import Agent

        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return ""
        proactive = (agent.config or {}).get("proactive", {}) or {}
        return (proactive.get("custom_instructions", "") or "").strip()

    async def _tick_failure_watchdog(self) -> None:
        """Detect schedules that fired but whose task never reached a terminal
        status (silent drop) and publish a Telegram alert.

        Symptom we are guarding against: the scheduled task gets created and
        even runs, but no completion event reaches the orchestrator (agent
        crashed, network blip, lost Redis pubsub). `success_count + fail_count`
        then drifts below `total_runs` and nobody notices — the operator only
        sees the missing artifact (e.g. morning podcast) hours later.

        We run once per hour, lazily, from inside the main scheduler loop.
        """
        import json as _json

        now = datetime.now(timezone.utc)
        if (
            self._failure_watchdog_last_run is not None
            and (now - self._failure_watchdog_last_run) < timedelta(hours=1)
        ):
            return
        self._failure_watchdog_last_run = now

        async with async_session_factory() as db:
            schedules = (
                await db.execute(select(Schedule).where(Schedule.enabled == True))  # noqa: E712
            ).scalars().all()
            for s in schedules:
                drift = s.total_runs - (s.success_count + s.fail_count)
                # Only alert on at least 2 outstanding runs to dampen the noise
                # from a single in-flight task that hasn't reported back yet.
                if drift < 2 or not s.last_run_at:
                    continue
                stale_for = now - s.last_run_at
                if stale_for < timedelta(hours=2):
                    continue
                # De-dup: only alert when drift increases beyond last alerted level.
                # Without this the same alert fires every hour indefinitely.
                if drift <= self._watchdog_alerted.get(s.id, 0):
                    continue
                if not self.redis or not self.redis.client:
                    continue
                safe_name = (
                    s.name.replace("\\", "\\\\")
                    .replace("_", "\\_")
                    .replace("*", "\\*")
                    .replace("`", "\\`")
                    .replace("[", "\\[")
                )
                payload = {
                    "text": (
                        f"⚠️ Schedule *{safe_name}* has {drift} unaccounted runs "
                        f"(total={s.total_runs}, ok={s.success_count}, "
                        f"fail={s.fail_count}).\n"
                        f"Last run {s.last_run_at.isoformat()} "
                        f"({int(stale_for.total_seconds() // 3600)}h ago)."
                    ),
                    "parse_mode": "Markdown",
                }
                try:
                    await self.redis.client.publish(
                        "telegram:notification", _json.dumps(payload)
                    )
                    self._watchdog_alerted[s.id] = drift
                except Exception as e:
                    print(f"[Scheduler] FailureWatchdog publish error: {e}")

    async def _stop_idle_agents(self) -> int:
        """Stop agents that have been idle longer than their configured limit.

        Resolution:
          - Global max via PlatformSettings key 'max_idle_minutes' (admin)
          - Per-agent override via agent.config['idle_stop_minutes'] (user)
          - Effective limit = min(per-agent, global). If neither set → no auto-stop.

        Idle is measured via agent.updated_at (TimestampMixin bumps it on any DB
        update — state changes, config edits, task assignments).
        """
        from datetime import datetime, timezone, timedelta
        from sqlalchemy import select
        from app.db.session import async_session_factory
        from app.models.agent import Agent, AgentState
        from app.models.platform_settings import PlatformSettings
        from app.models.task import Task, TaskStatus

        stopped = 0
        async with async_session_factory() as db:
            ps = await db.get(PlatformSettings, "max_idle_minutes")
            try:
                global_max = int(ps.value) if ps and ps.value else None
            except Exception:
                global_max = None
            if global_max is not None and global_max <= 0:
                global_max = None

            running_states = (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING)
            agents = (await db.execute(
                select(Agent).where(Agent.state.in_(running_states))
            )).scalars().all()

            now = datetime.now(timezone.utc)
            from app.core.agent_manager import AgentManager
            if not self.docker:
                return 0
            mgr = AgentManager(db, self.docker, self.redis)

            for agent in agents:
                if agent.state == AgentState.WORKING:
                    print(f"[IdleStop] Skip {agent.id} ({agent.name}) — DB state is WORKING")
                    continue

                try:
                    live_status = await self.redis.get_agent_status(agent.id)
                except Exception:
                    live_status = {}
                live_state = live_status.get("state")
                current_task = live_status.get("current_task")
                if live_state == "working" or current_task:
                    print(
                        f"[IdleStop] Skip {agent.id} ({agent.name}) — "
                        f"live state={live_state!r} current_task={current_task!r}"
                    )
                    continue

                active_task = (await db.execute(
                    select(Task.id)
                    .where(Task.agent_id == agent.id, Task.status == TaskStatus.RUNNING)
                    .limit(1)
                )).scalar_one_or_none()
                if active_task:
                    print(f"[IdleStop] Skip {agent.id} ({agent.name}) — task {active_task} is still RUNNING")
                    continue

                # Keep-warm: don't reap an agent that is a participant of a RUNNING meeting —
                # it idles between its turns but is needed again seconds later (avoids the
                # stop/restart churn and "[Agent hat nicht geantwortet]").
                from app.models.meeting_room import MeetingRoom as _MeetingRoom
                in_meeting = (await db.execute(
                    select(_MeetingRoom.id).where(
                        _MeetingRoom.state == "running",
                        _MeetingRoom.agent_ids.contains([agent.id]),
                    ).limit(1)
                )).scalar_one_or_none()
                if in_meeting:
                    print(f"[IdleStop] Skip {agent.id} ({agent.name}) — active in meeting {in_meeting}")
                    continue

                cfg = agent.config or {}
                per_agent = cfg.get("idle_stop_minutes")
                try:
                    per_agent = int(per_agent) if per_agent else None
                except Exception:
                    per_agent = None

                candidates = [v for v in (per_agent, global_max) if v and v > 0]
                if not candidates:
                    continue
                limit_min = min(candidates)

                last_update = agent.updated_at
                if last_update and last_update.tzinfo is None:
                    last_update = last_update.replace(tzinfo=timezone.utc)
                if not last_update:
                    continue

                idle_for = now - last_update
                if idle_for > timedelta(minutes=limit_min):
                    try:
                        await mgr.stop_agent(agent.id)
                        stopped += 1
                        print(f"[IdleStop] {agent.id} ({agent.name}) idle for {idle_for} > {limit_min}min — stopped")
                    except Exception as e:
                        print(f"[IdleStop] Failed to stop {agent.id}: {e}")

        return stopped


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
    """Return the next fire time (UTC) for a schedule.

    If cron_expression is set and croniter is available, the expression is
    evaluated in the schedule's IANA timezone so "0 6 * * *" fires at 06:00
    wall-clock time year-round (DST-aware), then converted back to UTC.
    Otherwise fall back to interval_seconds.
    """
    if schedule.cron_expression and _CRONITER_AVAILABLE:
        try:
            tz_name = getattr(schedule, "timezone", None) or "UTC"
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                print(f"[Scheduler] Unknown timezone '{tz_name}' — evaluating cron in UTC")
                tz = timezone.utc
            base = now.astimezone(tz)
            cron = croniter(schedule.cron_expression, base)
            return cron.get_next(datetime).astimezone(timezone.utc)
        except Exception as e:
            print(f"[Scheduler] Invalid cron expression '{schedule.cron_expression}': {e} — falling back to interval")
    return now + timedelta(seconds=max(schedule.interval_seconds, 60))
