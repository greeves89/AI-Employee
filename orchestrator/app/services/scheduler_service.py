import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from croniter import croniter
    _CRONITER_AVAILABLE = True
except ImportError:
    _CRONITER_AVAILABLE = False

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import PROACTIVE_PROMPT
from app.core.load_balancer import LoadBalancer
from app.core.task_router import TaskRouter
from app.db.session import resilient_session
from app.models.schedule import Schedule
from app.models.task import Task
from app.services.redis_service import RedisService
from app.services.watchdog import (
    as_utc,
    find_missed_schedules,
    find_stale_tasks,
    mark_task_stale,
    md_escape,
)

logger = logging.getLogger(__name__)

# Connect-level DB errors that resilient_session already retried and exhausted
# during a brief DB blip (restart/failover). They self-heal on the next tick, so
# the two bare DB-touching sub-ticks below log them as a clean WARNING instead of
# letting them surface as a full-traceback ERROR via the outer loop handler —
# matching how every other sub-tick in run() reports its own failures.
_TRANSIENT_DB_ERRORS = (OperationalError, DBAPIError, ConnectionError, TimeoutError)

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
        self._reflection_counter = 0
        self._reflection_service = None
        self._codex_refresh_counter = 0
        # Per-schedule drift value at which we last alerted; prevents hourly spam
        # for a stuck schedule — only re-alerts when drift increases.
        self._watchdog_alerted: dict[str, int] = {}
        # Per-schedule missed slot (next_run_at iso) already alerted; prevents
        # re-alerting the same missed window every 30s tick.
        self._missed_alerted: dict[str, str] = {}

    async def run(self) -> None:
        """Main loop - checks every 30s. Runs schedules always, GC every 60s,
        knowledge-feeds every 5 minutes, trend scan every 24h."""
        logger.info("[Scheduler] Service started")
        from app.services.knowledge_feed_service import KnowledgeFeedService
        from app.services.trend_service import TrendService
        from app.config import settings as _settings
        feed_service = KnowledgeFeedService(self.redis)
        trend_service = TrendService(self.redis, github_token=_settings.github_token)

        while True:
            try:
                # Missed-schedule watchdog runs BEFORE _check_due_schedules, which
                # would otherwise fire+advance the slipped run and hide the miss.
                try:
                    await self._tick_missed_schedule_watchdog()
                except Exception as e:
                    logger.warning("[Scheduler] MissedScheduleWatchdog error: %s", e)
                try:
                    await self._check_due_schedules()
                except _TRANSIENT_DB_ERRORS as e:
                    logger.warning(
                        "[Scheduler] DueSchedules DB unavailable (transient, "
                        "retrying next tick): %s", e,
                    )
                # Tagesplan: jeder Block mit Uhrzeit MUSS einen Ausloeser haben.
                try:
                    armed = await self._arm_plan_blocks()
                    if armed:
                        logger.info("[Scheduler] %s Plan-Block/Bloecke scharf gestellt", armed)
                except _TRANSIENT_DB_ERRORS as e:
                    logger.warning("[Scheduler] Plan-Bloecke DB unavailable (transient): %s", e)
                except Exception as e:
                    logger.warning("[Scheduler] Plan-Bloecke scharf stellen fehlgeschlagen: %s", e)
                # Stale-task watchdog: flag RUNNING tasks with no heartbeat >30min.
                try:
                    await self._tick_stale_task_watchdog()
                except Exception as e:
                    logger.warning("[Scheduler] StaleTaskWatchdog error: %s", e)
                # Workflow engine (#392): start cron-due workflows + advance active runs.
                try:
                    await self._start_due_workflows()
                    await self._advance_workflow_runs()
                except _TRANSIENT_DB_ERRORS as e:
                    logger.warning("[Scheduler] Workflow DB unavailable (transient): %s", e)
                except Exception as e:
                    logger.warning("[Scheduler] Workflow advance error: %s", e)
                try:
                    started = await self._start_due_followups()
                    if started:
                        logger.info("[Scheduler] Auto-started %s follow-up meeting(s)", started)
                except Exception as e:
                    logger.warning("[Scheduler] Follow-up auto-start error: %s", e)
                # Taskforce integration: dispatch the assemble step once every build
                # sub-task of a deliverable meeting is done.
                try:
                    integrated = await self._dispatch_due_integrations()
                    if integrated:
                        logger.info("[Scheduler] Dispatched %s taskforce integration(s)", integrated)
                except Exception as e:
                    logger.warning("[Scheduler] Taskforce integration error: %s", e)
                self._gc_counter += 30
                if self._gc_counter >= _GC_INTERVAL_SECONDS:
                    self._gc_counter = 0
                    try:
                        await self._gc_expired_tasks()
                    except _TRANSIENT_DB_ERRORS as e:
                        logger.warning(
                            "[Scheduler] GC DB unavailable (transient): %s", e,
                        )
                self._idle_stop_counter += 30
                if self._idle_stop_counter >= 300:  # every 5 min
                    self._idle_stop_counter = 0
                    try:
                        n = await self._stop_idle_agents()
                        if n > 0:
                            logger.info("[Scheduler] IdleStop: stopped %s idle agent(s)", n)
                    except Exception as e:
                        logger.warning("[Scheduler] IdleStop error: %s", e)
                self._feeds_counter += 30
                if self._feeds_counter >= 300:  # every 5 min
                    self._feeds_counter = 0
                    try:
                        summary = await feed_service.tick()
                        if summary.get("ran", 0) > 0:
                            logger.info(
                                "[Scheduler] KnowledgeFeeds: ran=%s new=%s err=%s",
                                summary["ran"], summary["new_items"], summary["errors"],
                            )
                    except Exception as e:
                        logger.warning("[Scheduler] KnowledgeFeeds error: %s", e)
                # Missed-run watchdog: catches schedules whose task never reported
                # a terminal status (silent drops). Self-throttles to once per hour.
                try:
                    await self._tick_failure_watchdog()
                except Exception as e:
                    logger.warning("[Scheduler] FailureWatchdog error: %s", e)
                # Trend scan: runs daily (TrendService.tick() self-throttles)
                try:
                    result = await trend_service.tick()
                    if not result.get("skipped") and result.get("generated", 0) > 0:
                        logger.info(
                            "[Scheduler] TrendScan: scanned=%s new=%s generated=%s err=%s",
                            result["scanned"], result["new"], result["generated"], result["errors"],
                        )
                except Exception as e:
                    logger.warning("[Scheduler] TrendScan error: %s", e)
                # "Dreaming": refresh adaptive user profiles from memories (gated)
                self._dreaming_counter += 30
                if self._dreaming_counter >= _DREAMING_INTERVAL_SECONDS:
                    self._dreaming_counter = 0
                    try:
                        n = await self._run_dreaming()
                        if n > 0:
                            logger.info("[Scheduler] Dreaming: refreshed %s user profile(s)", n)
                    except Exception as e:
                        logger.warning("[Scheduler] Dreaming error: %s", e)
                # Reflection ("Nachtschicht"): nightly transcript reflection (gated,
                # runs once per day at the configured local hour). Checked every 5 min.
                self._reflection_counter += 30
                if self._reflection_counter >= 300:
                    self._reflection_counter = 0
                    try:
                        if self._reflection_service is None:
                            from app.services.reflection_service import ReflectionService
                            self._reflection_service = ReflectionService(self.redis)
                        result = await self._reflection_service.tick()
                        if result:
                            logger.info("[Scheduler] Reflection: %s", result)
                    except Exception as e:
                        logger.warning("[Scheduler] Reflection error: %s", e)

                # Codex token: keep the shared ChatGPT auth fresh CENTRALLY (single
                # thread) so agents never refresh the single-use token concurrently
                # (which killed all Codex agents on a simultaneous "Update All").
                # Checked every 2h; only actually refreshes when near expiry (~8 days).
                self._codex_refresh_counter += 30
                if self._codex_refresh_counter >= 7200:
                    self._codex_refresh_counter = 0
                    try:
                        from app.services.codex_auth_service import CodexAuthService
                        await CodexAuthService().ensure_fresh()
                    except Exception as e:
                        logger.warning("[Scheduler] Codex refresh error: %s", e)
            except Exception as e:
                logger.error("[Scheduler] ERROR: %s", e, exc_info=True)
            await asyncio.sleep(30)

    async def _start_due_followups(self) -> int:
        """Auto-start idle follow-up meeting rooms — EVENT-BASED: start once the agents
        have FINISHED their assigned tasks from the parent meeting (the agents reliably
        complete tasks; they don't always tick the TODOs, so we key on task completion),
        or, as a safety net, once scheduled_for (the cap) is reached."""
        from datetime import datetime, timezone
        from sqlalchemy import select, and_, or_
        from app.db.session import resilient_session
        from app.models.meeting_room import MeetingRoom
        from app.models.task import Task, TaskStatus
        from app.api.meeting_rooms import _run_meeting, _start_moderator_container, _running_rooms

        now = datetime.now(timezone.utc)
        started = 0
        async with resilient_session() as db:
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
                logger.info("[Scheduler] Auto-started follow-up %s (%s): %s", room.id, reason, room.name)
        return started

    async def _dispatch_due_integrations(self) -> int:
        """For deliverable/taskforce meetings: once every build sub-task is done,
        dispatch the coordinator's integration task (assemble the shared work dir into
        one runnable deliverable). Fires once per meeting (deliverable_integrated guard)."""
        from sqlalchemy import select, and_
        from app.db.session import resilient_session
        from app.models.meeting_room import MeetingRoom
        from app.models.task import Task, TaskStatus
        from app.api.meeting_rooms import dispatch_integration_task

        dispatched = 0
        async with resilient_session() as db:
            rooms = (await db.execute(
                select(MeetingRoom).where(and_(
                    MeetingRoom.deliverable == True,
                    MeetingRoom.deliverable_integrated == False,
                    MeetingRoom.is_active == True,
                ))
            )).scalars().all()
            for room in rooms:
                # Only the build sub-tasks (source='meeting'); the integration task
                # itself carries source='meeting_integration' and must not gate itself.
                rows = (await db.execute(
                    select(Task.status, Task.metadata_).where(
                        Task.metadata_.op("->>")("room_id") == room.id
                    )
                )).all()
                build = [s for (s, m) in rows if (m or {}).get("source") == "meeting"]
                # Need at least one build task, and all of them terminal.
                if not build:
                    continue
                if not all(s not in (TaskStatus.PENDING, TaskStatus.RUNNING) for s in build):
                    continue
                try:
                    if await dispatch_integration_task(room.id, self.redis, self.docker):
                        dispatched += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("[Scheduler] Integration dispatch failed for %s: %s", room.id, e)
        return dispatched

    async def _run_dreaming(self) -> int:
        """'Dreaming': rebuild each active user's adaptive profile from their
        accumulated memories (heuristic, no LLM cost). Gated by ``dreaming_enabled``
        (default off). Per-user failures are isolated — never break the loop."""
        from app.services.settings_service import SettingsService
        from app.services.profile_extractor import extract_profile
        from app.models.agent import Agent
        async with resilient_session() as db:
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
        async with resilient_session() as db:
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
                logger.info("[Scheduler] GC: evicted %s expired task(s)", len(expired))
            except Exception as e:
                logger.warning("[Scheduler] GC error: %s", e)

    async def _check_due_schedules(self) -> None:
        now = datetime.now(timezone.utc)

        async with resilient_session() as db:
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
                schedule_id = schedule.id
                try:
                    await self._execute_schedule(db, router, schedule, now)
                    await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.warning("[Scheduler] Failed to execute schedule %s: %s", schedule_id, e)

    async def _start_due_workflows(self) -> None:
        """Start a run for every enabled workflow whose cron trigger just fired (#392)."""
        from datetime import datetime, timedelta, timezone

        from croniter import croniter
        from sqlalchemy import select

        from app.db.session import resilient_session
        from app.models.workflow import Workflow
        from app.services.workflow_engine import start_run

        now = datetime.now(timezone.utc)
        async with resilient_session() as db:
            wfs = (await db.execute(
                select(Workflow).where(Workflow.enabled.is_(True))
            )).scalars().all()
            for wf in wfs:
                trig = wf.trigger or {}
                cron = trig.get("cron")
                if not cron or not (wf.definition or {}).get("start"):
                    continue
                try:
                    last = trig.get("last_run")
                    last_dt = datetime.fromisoformat(last) if last else (now - timedelta(hours=1))
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    prev = croniter(cron, now).get_prev(datetime)
                    if prev.tzinfo is None:
                        prev = prev.replace(tzinfo=timezone.utc)
                    if prev > last_dt:
                        await start_run(wf, db)
                        wf.trigger = {**trig, "last_run": now.isoformat()}
                        await db.commit()
                        logger.info("[Scheduler] Workflow %s cron-triggered", wf.id)
                except Exception as e:
                    logger.warning("[Scheduler] Workflow cron %s error: %s", wf.id, e)

    async def _advance_workflow_runs(self) -> None:
        """Advance every active workflow run one move (#392)."""
        from app.db.session import resilient_session
        from app.models.workflow import Workflow, WorkflowRun
        from app.services.workflow_engine import advance_run
        from sqlalchemy import select

        async with resilient_session() as db:
            runs = (await db.execute(
                select(WorkflowRun).where(WorkflowRun.status == "running")
            )).scalars().all()
            if not runs:
                return
            lb = LoadBalancer(self.redis)
            router = TaskRouter(db, self.redis, lb, docker_service=self.docker)
            for run in runs:
                wf = (await db.execute(
                    select(Workflow).where(Workflow.id == run.workflow_id)
                )).scalar_one_or_none()
                if not wf:
                    run.status = "failed"
                    run.error = "Workflow gelöscht"
                    await db.commit()
                    continue
                await advance_run(run, wf, db, router)

    async def _execute_schedule(
        self,
        db: AsyncSession,
        router: TaskRouter,
        schedule: Schedule,
        now: datetime,
    ) -> None:
        """Create a task from a schedule and advance next_run_at."""
        # A STOPPED agent must not be driven. Without this check the schedule fired on
        # anyway and every run died immediately — at the customer two agents piled up
        # 337 failed runs over four weeks, one per hour, and nobody noticed. A stopped
        # agent is off duty; the schedule keeps its rhythm and resumes when it starts.
        if schedule.agent_id:
            from app.core import agent_duty
            from app.models.agent import Agent
            from app.services import duty_service

            duty_agent = (await db.execute(
                select(Agent).where(Agent.id == schedule.agent_id)
            )).scalar_one_or_none()
            if duty_agent is not None:
                queue_depth = await self.redis.get_queue_depth(schedule.agent_id)
                stale = await self._stale_task_count(db, schedule.agent_id, now)
                duty = agent_duty.assess(
                    duty_agent, queue_depth=queue_depth, stale_tasks=stale,
                    now=now, schedule_active=schedule.enabled,
                )
                if duty["state"] != agent_duty.OK:
                    # Ausfall/Blockade: die Arbeit muss jemand anders uebernehmen,
                    # sonst bleibt sie liegen und niemand merkt es.
                    if agent_duty.needs_handover(duty):
                        await duty_service.escalate_failure(db, self.redis, duty_agent, duty)
                    schedule.next_run_at = _calc_next_run(schedule, now)
                    logger.info(
                        "[Scheduler] %s uebersprungen — Agent %s: %s (%s)",
                        schedule.name, schedule.agent_id, duty["state"], duty["reason"],
                    )
                    return
                # Arbeitsfaehig — aber schweigt sein Ansprechpartner seit Tagen?
                await duty_service.escalate_silence(db, self.redis, duty_agent)

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
                logger.info(
                    "[Scheduler] Proactive %s skipped - agent busy (queue=%s, task=%r)",
                    schedule.name, queue_depth, current_task,
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
            logger.info("[Scheduler] Meeting schedule %s executed", schedule.name)
            return

        # For proactive schedules: always use the latest PROACTIVE_PROMPT from code
        # so prompt improvements apply immediately to all agents without DB migration.
        # Per-agent additions (admin/user editable in the UI) are appended to the
        # code base — stored as data in agent.config, never duplicating the base.
        is_proactive = schedule.name.startswith("[Proactive]")
        if is_proactive:
            prompt = PROACTIVE_PROMPT
            proactive_config = await self._proactive_config(db, schedule.agent_id)
            hours_note = _contact_hours_note(proactive_config)
            if hours_note:
                prompt = prompt + "\n\n" + hours_note
            # WHAT this agent owns. Without it the run can only work off todos someone
            # else filed — it plans nothing of its own and goes idle the moment the
            # list is empty. STEP 1 derives the day plan from this block.
            from app.core.responsibilities import responsibilities_note
            duties_note = responsibilities_note(proactive_config)
            if duties_note:
                prompt = prompt + "\n\n" + duties_note
            # Ohne Auftrag kann der Lauf NICHTS zustande bringen: kein Einrichtungsstand,
            # keine Verantwortungsbereiche → keine Arbeit, aus der sich ein Tag bauen liesse.
            # Frueher lief er trotzdem, kostete Modell-Zeit und meldete brav "nichts zu tun"
            # (beim Kunden 493 Laeufe, 51 USD, null Ergebnis). Jetzt wird der Lauf gar nicht
            # erst gestartet — stattdessen bekommt der Besitzer EINE Benachrichtigung, und
            # die Agentenkachel traegt ein Ausrufezeichen.
            from app.core.onboarding import is_onboarded, has_duties, onboarding_note
            from app.models.agent import Agent as _Agent
            _agent = (await db.execute(
                select(_Agent).where(_Agent.id == schedule.agent_id)
            )).scalar_one_or_none() if schedule.agent_id else None
            if _agent is not None and not (is_onboarded(_agent) and has_duties(_agent)):
                await self._nudge_missing_assignment(db, _agent)
                schedule.next_run_at = _calc_next_run(schedule, now)
                logger.info(
                    "[Scheduler] %s uebersprungen — Agent %s hat keinen Auftrag "
                    "(eingerichtet=%s, Bereiche=%s)",
                    schedule.name, _agent.id, is_onboarded(_agent), has_duties(_agent),
                )
                return
            ob_note = onboarding_note(_agent)
            if ob_note:
                prompt = prompt + "\n" + ob_note
            # Seine eigene Lage (Dienstzeit, Ueberlast, abwesender Ansprechpartner)
            # gehoert IHM in den Prompt — nicht nur in unsere Logs.
            if duty_agent is not None:
                d_note = agent_duty.duty_note(duty_agent, duty, now=now)
                if d_note:
                    prompt = prompt + "\n" + d_note
            extra = (proactive_config.get("custom_instructions", "") or "").strip()
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

        # Plan-Block: der Kalender soll zeigen, dass er LAEUFT — sonst steht dort ewig
        # "geplant", obwohl die Arbeit schon vorbei ist.
        if schedule.name.startswith("[Plan] "):
            from app.models.agent_plan_item import AgentPlanItem
            block = (await db.execute(
                select(AgentPlanItem).where(AgentPlanItem.schedule_id == schedule.id)
            )).scalar_one_or_none()
            if block is not None:
                block.status = "running"
                block.task_id = task.id

        # Advance schedule. Einmal-Laeufe (kein Cron, Intervall 0) schalten sich danach
        # ab — sonst stuende next_run_at sofort wieder in der Vergangenheit und der Block
        # feuerte im 30-Sekunden-Takt weiter.
        schedule.last_run_at = now
        schedule.total_runs += 1
        if not schedule.cron_expression and schedule.interval_seconds == 0:
            schedule.enabled = False
            schedule.next_run_at = now
        else:
            schedule.next_run_at = _calc_next_run(schedule, now)

        logger.info(
            "[Scheduler] %s triggered task %s, next run at %s",
            schedule.name, task.id, schedule.next_run_at.isoformat(),
        )

    async def _arm_plan_blocks(self) -> int:
        """Sicherstellen, dass jeder geplante Block mit Uhrzeit einen Ausloeser hat.

        Der Block LEGT seinen Einmal-Zeitplan beim Planen selbst an. Aber Bloecke aus
        aelteren Fassungen (und alles, was beim Schreiben schiefging) haetten keinen —
        sie staenden im Kalender und wuerden nie laufen. Genau das ist passiert: der
        Nutzer sah seinen Plan und fragte „wieso macht der nichts?".

        Deshalb hier die Invariante statt einer einmaligen Nachbesserung: Block mit
        Uhrzeit ⇒ Zeitplan. Vergangene Zeiten feuern sofort — nachholen ist richtig,
        stillschweigend verfallen lassen waere es nicht.
        """
        import uuid as _uuid
        from datetime import date as _date

        from app.models.agent_plan_item import AgentPlanItem

        armed = 0
        async with resilient_session() as db:
            # Zuerst nachziehen, was fertig ist — sonst haengt der Kalender auf "laeuft".
            settled = 0
            running = (await db.execute(
                select(AgentPlanItem).where(AgentPlanItem.status == "running")
            )).scalars().all()
            for item in running:
                if not item.task_id:
                    item.status = "done"
                    settled += 1
                    continue
                task = (await db.execute(
                    select(Task).where(Task.id == item.task_id)
                )).scalar_one_or_none()
                state = str(getattr(getattr(task, "status", ""), "value", getattr(task, "status", ""))).lower()
                if task is None or state in ("completed", "failed", "cancelled"):
                    item.status = "done"
                    settled += 1
            # Und die Bloecke, deren Zeitplan schon gefeuert hat, ohne dass sie es
            # mitbekommen haben (Laeufe von vor dieser Rueckmeldung). Ohne das stuenden
            # sie fuer immer auf "geplant", obwohl die Arbeit gelaufen ist.
            missed = (await db.execute(
                select(AgentPlanItem, Schedule)
                .join(Schedule, Schedule.id == AgentPlanItem.schedule_id)
                .where(AgentPlanItem.status == "planned", Schedule.total_runs > 0)
            )).all()
            for item, sched in missed:
                task = (await db.execute(
                    select(Task)
                    .where(Task.agent_id == item.agent_id,
                           Task.title.like(f"%{sched.name[:40]}%"))
                    .order_by(Task.created_at.desc()).limit(1)
                )).scalar_one_or_none()
                state = str(getattr(getattr(task, "status", ""), "value", getattr(task, "status", ""))).lower()
                item.task_id = getattr(task, "id", None)
                item.status = "running" if state in ("pending", "queued", "running") else "done"
                settled += 1
            if settled:
                await db.commit()

            orphans = (await db.execute(
                select(AgentPlanItem).where(
                    AgentPlanItem.status == "planned",
                    AgentPlanItem.planned_start.isnot(None),
                    AgentPlanItem.schedule_id.is_(None),
                    AgentPlanItem.plan_date >= _date.today(),
                )
            )).scalars().all()
            for item in orphans:
                schedule_id = _uuid.uuid4().hex[:8]
                db.add(Schedule(
                    id=schedule_id,
                    name=f"[Plan] {item.title[:60]}",
                    prompt=(
                        f"Das ist ein Block aus DEINEM eigenen Tagesplan "
                        f"({item.planned_start:%H:%M}, ca. {item.estimated_minutes} Min, "
                        f"Priorität {item.priority}):\n\n{item.title}\n"
                        + (f"\nPräzisierung: {item.notes}\n" if item.notes else "")
                        + "\nArbeite ihn JETZT ab — vollständig, nicht nur beschreiben. Ist er "
                        "größer als gedacht, mach den ersten sinnvollen Schritt fertig und halte "
                        "den Rest in `.agent_state.md` fest. Melde am Ende in zwei Sätzen das "
                        "Ergebnis und lege erzeugte Dateien nach /workspace/transfer/."
                    ),
                    interval_seconds=0,
                    priority=0 if item.priority == "high" else 1,
                    agent_id=item.agent_id,
                    enabled=True,
                    next_run_at=item.planned_start,
                ))
                item.schedule_id = schedule_id
                armed += 1
            if armed:
                # Ohne das faellt beim Verlassen der Sitzung alles weg: die Zeitplaene
                # waren angelegt und beim naechsten Blick wieder verschwunden.
                await db.commit()
        return armed

    async def _stale_task_count(self, db: AsyncSession, agent_id: str, now: datetime) -> int:
        """Wie viele Aufgaben dieses Agenten haengen? Nutzt die Watchdog-Definition,
        damit 'haengt' ueberall dasselbe heisst."""
        try:
            stale = await find_stale_tasks(db, now)
            return sum(1 for t in stale if t.agent_id == agent_id)
        except Exception:  # noqa: BLE001 — im Zweifel nicht blockieren
            logger.debug("[Scheduler] Stale-Zaehlung fehlgeschlagen", exc_info=True)
            return 0

    async def _nudge_missing_assignment(self, db: AsyncSession, agent) -> None:
        """EINE Benachrichtigung an den Besitzer, wenn ein Agent ohne Auftrag dasteht.

        Gedrosselt auf einmal pro 12 Stunden (gleicher Takt wie die Meldebremse des
        Agenten) — sonst bekommt der Nutzer bei stuendlichem Zeitplan 24 Hinweise am Tag
        und schaltet den Agenten ab, statt ihn einzurichten.
        """
        from app.models.notification import Notification

        key = f"onboarding_nudge:{agent.id}"
        try:
            if await self.redis.client.exists(key):
                return
            await self.redis.client.setex(key, 12 * 3600, "1")
        except Exception:  # noqa: BLE001 — ohne Redis lieber einmal zu viel als gar nicht
            logger.debug("[Scheduler] Nudge-Drossel nicht verfuegbar", exc_info=True)

        from app.core.onboarding import is_onboarded
        fehlt = (
            "Er weiss noch nicht, wofuer er da ist."
            if not is_onboarded(agent)
            else "Ihm fehlen die Verantwortungsbereiche — er hat also keine wiederkehrenden Aufgaben."
        )
        db.add(Notification(
            agent_id=agent.id,
            type="warning",
            title=f"{agent.name} wartet auf seinen Auftrag",
            message=(
                f"{fehlt} Der proaktive Lauf wurde deshalb uebersprungen — ohne Auftrag "
                f"kann er nichts tun. Sag ihm im Chat, welche Rolle er hat und welche "
                f"Aufgaben er dauerhaft uebernimmt, oder trage die Bereiche direkt in "
                f"seinen Einstellungen ein."
            )[:240],
            priority="normal",
            action_url=f"/agents/{agent.id}?tab=settings",
            meta={"reason": "missing_assignment"},
        ))
        logger.info("[Scheduler] Hinweis an Besitzer: %s hat keinen Auftrag", agent.id)

    async def _proactive_config(
        self, db: AsyncSession, agent_id: str | None
    ) -> dict:
        """Per-agent proactive settings from agent.config['proactive'] (custom
        instructions, contact hours). Read fresh at fire time so the base
        PROACTIVE_PROMPT stays centralized in code (one source of truth) while
        each agent carries its own additions as plain data.
        """
        if not agent_id:
            return {}
        from sqlalchemy import select
        from app.models.agent import Agent

        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return {}
        return (agent.config or {}).get("proactive", {}) or {}

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

        async with resilient_session() as db:
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
                    logger.warning("[Scheduler] FailureWatchdog publish error: %s", e)

    async def _tick_stale_task_watchdog(self) -> None:
        """Mark RUNNING tasks that stopped heart-beating (>30min) as stale.

        A worker that crashes mid-job (container OOM, network drop) leaves its
        task pinned in RUNNING forever. updated_at stops advancing, so we flip
        such tasks to FAILED with a `stale` metadata flag and alert the owner —
        instead of the operator discovering a missing artifact hours later.
        """
        import json as _json

        now = datetime.now(timezone.utc)
        async with resilient_session() as db:
            stale = await find_stale_tasks(db, now)
            if not stale:
                return
            from app.models.notification import Notification

            for task in stale:
                mark_task_stale(task, now)
                db.add(
                    Notification(
                        agent_id=task.agent_id or "system",
                        type="error",
                        title="Task stale (kein Heartbeat)",
                        message=(
                            f'Task "{task.title}" hat seit über 30min kein '
                            "Lebenszeichen gesendet und wurde als stale markiert."
                        )[:240],
                        priority="high",
                        action_url=f"/tasks/{task.id}",
                        meta={"type": "task_stale", "task_id": task.id},
                    )
                )
                if self.redis and self.redis.client:
                    payload = {
                        "text": (
                            f"⚠️ Task *{md_escape(task.title)}* stale — kein "
                            f"Heartbeat >30min (id `{task.id}`), als fehlgeschlagen markiert."
                        ),
                        "parse_mode": "Markdown",
                    }
                    try:
                        await self.redis.client.publish(
                            "telegram:notification", _json.dumps(payload)
                        )
                    except Exception as e:
                        logger.warning("[Scheduler] StaleTaskWatchdog publish error: %s", e)
            await db.commit()
            logger.info("[Scheduler] StaleTaskWatchdog: marked %s task(s) stale", len(stale))

    async def _tick_missed_schedule_watchdog(self) -> None:
        """Alert on enabled schedules whose fire window was missed (>5min late).

        Under normal operation the main loop fires due schedules every 30s, so
        next_run_at is always in the future. A next_run_at that slipped well
        into the past means the scheduler was down during the window (container
        restart) — the run is caught up late, but the owner is told it slipped.
        """
        import json as _json

        now = datetime.now(timezone.utc)
        async with resilient_session() as db:
            missed = await find_missed_schedules(db, now)
            for s in missed:
                slot_key = as_utc(s.next_run_at).isoformat()
                if self._missed_alerted.get(s.id) == slot_key:
                    continue
                self._missed_alerted[s.id] = slot_key
                if not self.redis or not self.redis.client:
                    continue
                late_min = int((now - as_utc(s.next_run_at)).total_seconds() // 60)
                payload = {
                    "text": (
                        f"⚠️ Schedule *{md_escape(s.name)}* verpasst — geplant "
                        f"{slot_key} (überfällig {late_min} min). Wird nachgeholt."
                    ),
                    "parse_mode": "Markdown",
                }
                try:
                    await self.redis.client.publish(
                        "telegram:notification", _json.dumps(payload)
                    )
                except Exception as e:
                    logger.warning("[Scheduler] MissedScheduleWatchdog publish error: %s", e)

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
        from app.db.session import resilient_session
        from app.models.agent import Agent, AgentState
        from app.models.platform_settings import PlatformSettings
        from app.models.task import Task, TaskStatus

        stopped = 0
        async with resilient_session() as db:
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
                    logger.debug("[IdleStop] Skip %s (%s) — DB state is WORKING", agent.id, agent.name)
                    continue

                # Always-on agents are never idle-reaped (same flag as the user-lifecycle sweep).
                if (agent.config or {}).get("always_on"):
                    continue

                try:
                    live_status = await self.redis.get_agent_status(agent.id)
                except Exception:
                    live_status = {}
                live_state = live_status.get("state")
                current_task = live_status.get("current_task")
                if live_state == "working" or current_task:
                    logger.debug(
                        "[IdleStop] Skip %s (%s) — live state=%r current_task=%r",
                        agent.id, agent.name, live_state, current_task,
                    )
                    continue

                active_task = (await db.execute(
                    select(Task.id)
                    .where(Task.agent_id == agent.id, Task.status == TaskStatus.RUNNING)
                    .limit(1)
                )).scalar_one_or_none()
                if active_task:
                    logger.debug("[IdleStop] Skip %s (%s) — task %s is still RUNNING", agent.id, agent.name, active_task)
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
                    logger.debug("[IdleStop] Skip %s (%s) — active in meeting %s", agent.id, agent.name, in_meeting)
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
                        logger.info("[IdleStop] %s (%s) idle for %s > %smin — stopped", agent.id, agent.name, idle_for, limit_min)
                    except Exception as e:
                        logger.warning("[IdleStop] Failed to stop %s: %s", agent.id, e)

        return stopped


    async def _execute_meeting_schedule(self, db: AsyncSession, schedule: Schedule) -> None:
        """Create and start a scheduled meeting room."""
        import json as _json
        import uuid as _uuid
        from app.models.meeting_room import MeetingRoom

        try:
            config = _json.loads(schedule.prompt[len("__meeting__:"):])
        except Exception as e:
            logger.warning("[Scheduler] Bad meeting config for %s: %s", schedule.id, e)
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
        logger.info("[Scheduler] Started scheduled meeting %s: %s", room.id, room.name)


def _contact_hours_note(proactive_config: dict) -> str:
    """Render the agent's configured Ansprechpartner working hours as a prompt
    block, or "" if none are set (PROACTIVE_PROMPT STEP 4 then treats every run
    as off-hours by default).

    Only formats the note — whether "now" falls inside the window is left to the
    agent's own judgment at runtime; the orchestrator has no reliable way to know
    which moment in the run the agent will act on a decision that needs sign-off.
    """
    hours = (proactive_config or {}).get("contact_hours") or {}
    start = (hours.get("start") or "").strip()
    end = (hours.get("end") or "").strip()
    if not start or not end:
        return ""
    tz = (hours.get("timezone") or "UTC").strip() or "UTC"
    return (
        "## Ansprechpartner-Erreichbarkeit\n"
        f"Erreichbar {start}–{end} ({tz}). Außerhalb dieses Fensters gilt STEP 4 "
        "(Day/Night-Regel) als Off-Hours."
    )


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
                logger.warning("[Scheduler] Unknown timezone '%s' — evaluating cron in UTC", tz_name)
                tz = timezone.utc
            base = now.astimezone(tz)
            cron = croniter(schedule.cron_expression, base)
            return cron.get_next(datetime).astimezone(timezone.utc)
        except Exception as e:
            logger.warning("[Scheduler] Invalid cron expression '%s': %s — falling back to interval", schedule.cron_expression, e)
    return now + timedelta(seconds=max(schedule.interval_seconds, 60))


# Generous cap on enumerated fire times per schedule per call — protects against
# a pathological cron expression (e.g. "* * * * *" over a wide range) or a huge
# requested range; a real day-timeline range never needs anywhere near this many.
_MAX_OCCURRENCES = 1000


def schedule_occurrences(schedule: "Schedule", range_start: datetime, range_end: datetime) -> list[datetime]:
    """All fire times (UTC) of `schedule` within [range_start, range_end).

    Purely mathematical from cron_expression/interval_seconds — independent of
    whether the schedule actually fired (Task rows are the record of that; this
    is "the plan"). Used to render planned-run markers on the activity
    timeline, including for past days where the plan may differ from what
    actually ran (schedule was paused/changed since).
    """
    if range_end <= range_start:
        return []
    occurrences: list[datetime] = []

    if schedule.cron_expression and _CRONITER_AVAILABLE:
        try:
            tz_name = getattr(schedule, "timezone", None) or "UTC"
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc
            cron = croniter(schedule.cron_expression, range_start.astimezone(tz))
            cron.get_prev(datetime)  # step back so a fire time exactly at range_start isn't missed
            for _ in range(_MAX_OCCURRENCES):
                nxt = cron.get_next(datetime).astimezone(timezone.utc)
                if nxt >= range_end:
                    break
                if nxt >= range_start:
                    occurrences.append(nxt)
        except Exception as e:
            logger.warning(
                "[Scheduler] Invalid cron expression '%s' while listing occurrences: %s",
                schedule.cron_expression, e,
            )
    elif schedule.interval_seconds and schedule.interval_seconds > 0 and schedule.next_run_at:
        step_s = schedule.interval_seconds
        anchor = schedule.next_run_at
        if anchor.tzinfo is None:  # SQLite drops tzinfo on round-trip; Postgres never does
            anchor = anchor.replace(tzinfo=timezone.utc)
        # Jump straight to the first candidate at-or-before range_start via
        # integer arithmetic instead of stepping one interval at a time —
        # range_start can be arbitrarily far from the anchor (e.g. a past day
        # for a schedule created recently).
        steps_to_start = math.floor((range_start - anchor).total_seconds() / step_s)
        t = anchor + timedelta(seconds=steps_to_start * step_s)
        while t < range_start:
            t += timedelta(seconds=step_s)
        for _ in range(_MAX_OCCURRENCES):
            if t >= range_end:
                break
            occurrences.append(t)
            t += timedelta(seconds=step_s)

    return occurrences
