import asyncio
import logging
import math
import uuid
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
from app.core.log_redaction import scrub_log
from app.core.task_router import TaskRouter
from app.db.session import resilient_session
from app.models.schedule import Schedule
from app.models.task import Task
from app.services.redis_service import RedisService
from app.services.watchdog import (
    as_utc,
    find_missed_schedules,
    is_sentinel_stale,
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

# Wie lange eine unbeantwortete Freigabe stehen bleibt, bevor sie verfaellt.
# Deutlich laenger als die laengste Wartezeit eines Agenten (15 min): eine
# Frage, die jemand abends sieht und morgens beantworten will, darf nicht ueber
# Nacht verschwinden.
_APPROVAL_TTL_HOURS = 24

# Consecutive failed DueSchedules ticks (30s cadence) before escalating to the
# user. 4 ticks (~2min) rather than 1: a single blip self-heals silently and is
# not worth an alert. Confirmed need via issue #601 (2026-08-15): a ~30min
# Postgres outage silently blocked every DueSchedules check during the 06:00
# job window with no escalation at all — the only reason it was caught was an
# unrelated 06:30 safety-net schedule set up separately for those two jobs.
# Schedules without such a safety net would simply have stayed silent.
_DUE_SCHEDULES_ALERT_THRESHOLD = 4

# OVERLOADED is usually a short-lived queue spike. Do not lose a daily cron slot
# immediately, but also do not keep a schedule in a retry loop forever.
_OVERLOAD_RETRY_MAX_ATTEMPTS = 2
_OVERLOAD_RETRY_DELAY = timedelta(minutes=12)
_OVERLOAD_RETRY_TTL_SECONDS = 6 * 3600

# Same idea for the other transient skips (off-duty hours, momentarily busy
# with another task): one collision at the exact cron tick used to cost the
# whole day, since next_run_at jumped straight to _calc_next_run(now) — see
# _retry_or_advance. A dispatch-lock collision resolves in seconds, not minutes,
# so it gets its own, much shorter delay tuned to the 30s tick interval.
_TRANSIENT_RETRY_MAX_ATTEMPTS = 2
_TRANSIENT_RETRY_DELAY = timedelta(minutes=12)
_LOCK_RETRY_MAX_ATTEMPTS = 3
_LOCK_RETRY_DELAY = timedelta(seconds=30)
_RETRY_REASONS = ("overload", "off_duty", "busy", "lock", "down")

# Eine Meldung pro Zeitplan und Stunde. Ein Zeitplan, der oefter als stuendlich
# laeuft, verwirft bei einer laengeren Stoerung sonst die ganze Nacht lang alle
# ~25 Minuten einen Slot und meldet jeden einzeln.
_DROPPED_SLOT_ALERT_COOLDOWN_SECONDS = 3600

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
        self._synthesis_service = None
        self._teams_counter = 0
        self._teams_gateway = None
        self._channel_responder = None
        self._teams_meetings = None
        self._slack_gateway = None
        self._discord_gateway = None
        self._codex_refresh_counter = 0
        # Rhythmus-Invariante wird alle 5 Minuten geprueft — beim ersten Tick sofort,
        # damit ein frisch gestarteter Orchestrator die Zeitplaene nicht erst spaeter anlegt.
        self._rhythm_counter = 300
        # Per-schedule drift value at which we last alerted; prevents hourly spam
        # for a stuck schedule — only re-alerts when drift increases.
        self._watchdog_alerted: dict[str, int] = {}
        # Einmal melden, wenn der Sentinel verstummt — nicht alle 30 Sekunden.
        self._sentinel_alerted: bool = False
        # Per-schedule missed slot (next_run_at iso) already alerted; prevents
        # re-alerting the same missed window every 30s tick.
        self._missed_alerted: dict[str, str] = {}
        # Consecutive failed DueSchedules ticks + whether we've already told the
        # user about the current outage (reset on the first successful tick).
        self._due_schedules_fail_streak = 0
        self._due_schedules_db_alerted = False

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
                    await self._tick_sentinel_liveness()
                except Exception as e:
                    logger.warning("[Scheduler] SentinelLiveness error: %s", e)
                try:
                    await self._check_due_schedules()
                    if self._due_schedules_fail_streak >= _DUE_SCHEDULES_ALERT_THRESHOLD:
                        logger.info(
                            "[Scheduler] DueSchedules DB recovered after %s failed tick(s)",
                            self._due_schedules_fail_streak,
                        )
                    self._due_schedules_fail_streak = 0
                    self._due_schedules_db_alerted = False
                except _TRANSIENT_DB_ERRORS as e:
                    self._due_schedules_fail_streak += 1
                    logger.warning(
                        "[Scheduler] DueSchedules DB unavailable (transient, "
                        "retrying next tick, %s consecutive): %s",
                        self._due_schedules_fail_streak, e,
                    )
                    if (
                        self._due_schedules_fail_streak >= _DUE_SCHEDULES_ALERT_THRESHOLD
                        and not self._due_schedules_db_alerted
                    ):
                        self._due_schedules_db_alerted = True
                        try:
                            await self._alert_due_schedules_down(self._due_schedules_fail_streak)
                        except Exception as alert_err:
                            logger.warning(
                                "[Scheduler] DueSchedules alert error: %s", alert_err,
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
                # Arbeitsrhythmus: proaktiver Agent ⇒ Abendplanung + Morgencheck.
                # Alle 5 Minuten genuegt — die Zeitplaene aendern sich nur, wenn der
                # Nutzer die Dienstzeit umstellt.
                self._rhythm_counter += 30
                if self._rhythm_counter >= 300:
                    self._rhythm_counter = 0
                    try:
                        n = await self._ensure_planning_rhythm()
                        if n:
                            logger.info("[Scheduler] %s Rhythmus-Zeitplan/-plaene angelegt", n)
                    except _TRANSIENT_DB_ERRORS as e:
                        logger.warning("[Scheduler] Rhythmus DB unavailable (transient): %s", e)
                    except Exception as e:
                        logger.warning("[Scheduler] Rhythmus sicherstellen fehlgeschlagen: %s", e)
                # Stale-task watchdog: flag RUNNING tasks with no heartbeat >30min.
                try:
                    await self._tick_stale_task_watchdog()
                except Exception as e:
                    logger.warning("[Scheduler] StaleTaskWatchdog error: %s", e)
                # Selbstheilung (#390): faellige Wiederholungen abschicken. Hier und
                # nicht in einem eigenen Dienst — die Wartezeit ist ohnehin auf 30
                # Sekunden genau, und ein zweiter Takt waere ein zweiter Ort, an dem
                # etwas haengenbleiben kann.
                try:
                    resent = await self._dispatch_due_retries()
                    if resent:
                        logger.info("[Scheduler] Selbstheilung: %s Wiederholung(en) abgeschickt", resent)
                except _TRANSIENT_DB_ERRORS as e:
                    logger.warning("[Scheduler] Selbstheilung DB unavailable (transient): %s", e)
                except Exception as e:
                    logger.warning("[Scheduler] Selbstheilung error: %s", e)
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
                        await self._expire_stale_approvals()
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
                        # %r + exc_info: ein TimeoutError hat einen LEEREN str(),
                        # sodass "Reflection error: " ohne jede Ursache im Log
                        # stand — genau das verschleierte die naechtlichen
                        # DB-Timeouts. Typname und Traceback muessen mit rein.
                        logger.warning("[Scheduler] Reflection error: %r", e, exc_info=True)

                    # Wochensynthese (#384) haengt am SELBEN Takt — ein eigener
                    # Scheduler waere ein zweites Uhrwerk fuer dieselbe Frage.
                    # Der Dienst prueft Wochentag und Stunde selbst und ist billig,
                    # wenn abgeschaltet.
                    try:
                        if self._synthesis_service is None:
                            from app.services.synthesis_service import WeeklySynthesisService
                            self._synthesis_service = WeeklySynthesisService(self.redis)
                        syn = await self._synthesis_service.tick()
                        if syn:
                            logger.info("[Scheduler] Wochensynthese: %s", syn)
                    except Exception as e:
                        logger.warning("[Scheduler] Wochensynthese-Fehler: %s", e)

                # Teams-Kanal: eingehende Nachrichten abfragen (Graph kennt kein
                # getUpdates). Eigener, kuerzerer Takt als die Nachtschicht — eine
                # Antwort erst nach fuenf Minuten waere kein Gespraech. Billig, wenn
                # kein Agent Teams eingeschaltet hat.
                self._teams_counter += 30
                if self._teams_counter >= 30:
                    self._teams_counter = 0
                    try:
                        if self._teams_gateway is None:
                            from app.core.channel_gateway import ChannelResponder
                            from app.services.discord_gateway import DiscordGateway
                            from app.services.slack_gateway import SlackGateway
                            from app.services.teams_gateway import TeamsGateway
                            self._teams_gateway = TeamsGateway(self.redis)
                            self._slack_gateway = SlackGateway(self.redis)
                            self._discord_gateway = DiscordGateway(self.redis)
                            self._channel_responder = ChannelResponder(self.redis)

                        # EIN Lauscher je Agent bedient alle abgefragten Kanaele.
                        await self._channel_responder.ensure_listeners(
                            await self._agents_with_channels()
                        )
                        result = await self._teams_gateway.tick()
                        if result:
                            logger.info("[Scheduler] Teams: %s", result)
                        slack_result = await self._slack_gateway.tick()
                        if slack_result:
                            logger.info("[Scheduler] Slack: %s", slack_result)
                        discord_result = await self._discord_gateway.tick()
                        if discord_result:
                            logger.info("[Scheduler] Discord: %s", discord_result)
                        # Termine: Agent als Beisitzer an den laufenden Termin-Chat
                        # haengen bzw. nach dem Termin das Transkript ablegen. Haengt am
                        # selben Takt und an derselben Chat-Liste wie der Teams-Eingang.
                        if self._teams_meetings is None:
                            from app.services.teams_meetings import TeamsMeetingService
                            self._teams_meetings = TeamsMeetingService(self.redis)
                        meeting_result = await self._teams_meetings.tick()
                        if meeting_result:
                            logger.info("[Scheduler] Termine: %s", meeting_result)
                    except Exception as e:
                        logger.warning("[Scheduler] Teams-Fehler: %s", e)

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

    async def _expire_stale_approvals(self) -> int:
        """Offene Freigaben verfallen lassen, auf die niemand mehr wartet.

        ``ApprovalStatus.EXPIRED`` stand seit jeher im Modell und wurde **nie**
        gesetzt: eine Anfrage blieb ewig offen, auch wenn der fragende Agent längst
        in seine Zeitgrenze gelaufen und der Lauf vorbei war. Auf einer Anlage waren
        so 570 Zeilen aufgelaufen — und in einer Liste mit 570 Einträgen findet
        niemand mehr die eine, die wirklich zählt.

        Die Frist ist bewusst deutlich länger als die längste Wartezeit eines
        Agenten (15 Minuten): eine Frage, die jemand am Abend sieht und morgens
        beantworten will, darf nicht über Nacht verfallen.
        """
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select

        from app.db.session import resilient_session
        from app.models.command_approval import ApprovalStatus, CommandApproval

        cutoff = datetime.now(timezone.utc) - timedelta(hours=_APPROVAL_TTL_HOURS)
        async with resilient_session() as db:
            rows = (await db.execute(
                select(CommandApproval).where(
                    CommandApproval.status == ApprovalStatus.PENDING,
                    CommandApproval.created_at < cutoff,
                ).limit(500)
            )).scalars().all()
            if not rows:
                return 0
            now = datetime.now(timezone.utc)
            for approval in rows:
                approval.status = ApprovalStatus.EXPIRED
                approval.resolved_at = now
                approval.user_response = (
                    f"Nicht beantwortet, nach {_APPROVAL_TTL_HOURS} h verfallen"
                )
            await db.commit()
        logger.info("[Freigaben] %s unbeantwortete Anfrage(n) verfallen", len(rows))
        return len(rows)

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

    async def _agents_with_channels(self) -> set:
        """Agenten mit mindestens einem abgefragten Kanal.

        Eine Abfrage fuer alle Kanaele — sonst laedt jeder Kanal die Agentenliste
        einzeln, dreissig Sekunden lang, immer wieder.
        """
        from app.models.agent import Agent as _Agent
        from app.services import slack_gateway as _slack
        from app.services import teams_gateway as _teams
        from app.services import teams_meetings as _meetings
        from app.services import whatsapp_gateway as _wa
        from sqlalchemy import select as _select

        async with resilient_session() as db:
            agents = (await db.execute(
                _select(_Agent).where(_Agent.user_id.isnot(None))
            )).scalars().all()
        return {
            a.id for a in agents
            if _teams.is_enabled(a) or _slack.is_enabled(a)
            or _wa.is_enabled(a) or _meetings.is_enabled(a)
        }

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
                # Ein gestoppter Agent (Idle-/UserLifecycle-Stop) ist kein
                # Ausfall, solange er sich wecken laesst: faellige Zeitplaene
                # und Kalender-Bloecke STARTEN ihn — vorher galt er als DOWN,
                # der Lauf verschwand spurlos und der Tick versuchte es alle
                # 30 s erneut (#632).
                if schedule.enabled and agent_duty._state_str(duty_agent) not in agent_duty._LIVE_STATES:
                    from app.core.agent_wakeup import ensure_agent_running
                    if await ensure_agent_running(schedule.agent_id, self.docker, self.redis):
                        try:
                            await db.refresh(duty_agent)
                        except Exception:  # noqa: BLE001 — Fake-DBs in Tests koennen kein refresh
                            duty_agent.state = "running"
                        logger.info(
                            "[Scheduler] %s — Agent %s war gestoppt und wurde fuer den faelligen Lauf geweckt",
                            schedule.name, schedule.agent_id,
                        )
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
                        await duty_service.escalate_failure(
                            db, self.redis, duty_agent, duty, lost_run=schedule.name,
                        )
                        # Der Ausfall kostet genau hier einen faelligen Lauf. Ohne
                        # Eintrag verschwindet er spurlos — kein Task, keine Liste,
                        # kein Zaehler (#632).
                        await duty_service.escalate_skipped_run(
                            db, self.redis, duty_agent, duty,
                            schedule_id=schedule.id, schedule_name=schedule.name,
                            slot=as_utc(schedule.next_run_at or now),
                        )
                        # Ohne Verschieben bliebe next_run_at in der Vergangenheit:
                        # jeder Tick meldet denselben Ausfall neu. Kurz nachsetzen
                        # (der Agent laesst sich vielleicht gleich wecken), dann
                        # regulaer weiterruecken — wie off_duty.
                        schedule.next_run_at = await self._retry_or_advance(
                            schedule, now, reason="down",
                            max_attempts=_TRANSIENT_RETRY_MAX_ATTEMPTS, delay=_TRANSIENT_RETRY_DELAY,
                        )
                    elif duty["state"] == agent_duty.OVERLOADED:
                        # Kein Handover noetig (der Agent lebt, er ist nur beschaeftigt) —
                        # aber ohne Meldung verschwindet der uebersprungene Lauf spurlos (#605).
                        await duty_service.escalate_overload(
                            db, self.redis, duty_agent, duty, schedule.name,
                        )
                        schedule.next_run_at = await self._next_run_after_overload(
                            schedule, now
                        )
                    else:
                        # Weder Handover-wuerdig (DOWN/BLOCKED) noch ueberlastet —
                        # das ist heute nur OFF_DUTY (ausserhalb der Dienstzeit).
                        # Knapp daneben liegende Dienstzeiten oder eine kurz falsch
                        # gesetzte Uhrzeit kosten sonst sofort den ganzen Tag, statt
                        # es gleich nochmal zu versuchen.
                        schedule.next_run_at = await self._retry_or_advance(
                            schedule, now, reason="off_duty",
                            max_attempts=_TRANSIENT_RETRY_MAX_ATTEMPTS, delay=_TRANSIENT_RETRY_DELAY,
                        )
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
                schedule.next_run_at = await self._retry_or_advance(
                    schedule, now, reason="busy",
                    max_attempts=_TRANSIENT_RETRY_MAX_ATTEMPTS, delay=_TRANSIENT_RETRY_DELAY,
                )
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
        # Rhythmus-Laeufe (Abendplanung/Morgencheck) sind KEIN Sonderweg: sie bekommen
        # denselben Kontext-Anhang wie ein proaktiver Lauf — Erreichbarkeit, Bereiche,
        # Einrichtungsstand, eigene Lage —, nur mit einem anderen Auftrag im Kopf.
        from app.core import plan_rhythm
        is_proactive = schedule.name.startswith("[Proactive]")
        is_rhythm = schedule.name.startswith(plan_rhythm.SCHEDULE_PREFIX)
        if is_proactive or is_rhythm:
            proactive_config = await self._proactive_config(db, schedule.agent_id)
            # Ohne Auftrag kann der Lauf NICHTS zustande bringen: kein Einrichtungsstand,
            # keine Verantwortungsbereiche → keine Arbeit, aus der sich ein Tag bauen liesse.
            # Frueher lief er trotzdem, kostete Modell-Zeit und meldete brav "nichts zu tun"
            # (beim Kunden 493 Laeufe, 51 USD, null Ergebnis). Jetzt wird der Lauf gar nicht
            # erst gestartet — stattdessen bekommt der Besitzer EINE Benachrichtigung, und
            # die Agentenkachel traegt ein Ausrufezeichen.
            # Nur noch EINE Bedingung: hat er Verantwortungsbereiche. Der frueher
            # zusaetzlich gepruefte Einrichtungshaken war eine Falle, seit das
            # Einrichtungsgespraech entfallen ist — nichts konnte ihn mehr setzen,
            # also waeren die Laeufe eines Bestandsagenten fuer immer uebersprungen
            # worden.
            from app.core.onboarding import has_duties, onboarding_note
            from app.models.agent import Agent as _Agent
            _agent = (await db.execute(
                select(_Agent).where(_Agent.id == schedule.agent_id)
            )).scalar_one_or_none() if schedule.agent_id else None
            if _agent is not None and not has_duties(_agent):
                await self._nudge_missing_assignment(db, _agent)
                schedule.next_run_at = _calc_next_run(schedule, now)
                logger.info(
                    "[Scheduler] %s uebersprungen — Agent %s hat keine "
                    "Verantwortungsbereiche",
                    schedule.name, _agent.id,
                )
                return

            if is_rhythm:
                # Abends planen, morgens nachschaerfen — mit dem, was ueber Nacht lief.
                night = await self._night_runs(db, schedule.agent_id, now)
                plan_for = plan_rhythm.target_date(_agent, now)
                prompt = (
                    plan_rhythm.evening_prompt(_agent, plan_for, night)
                    if schedule.name == plan_rhythm.EVENING_SCHEDULE_NAME
                    else plan_rhythm.morning_prompt(_agent, plan_for, night)
                )
            else:
                prompt = PROACTIVE_PROMPT
            # Welche Phase gerade ist, gehoert in JEDEN Lauf: faellt der Abend-Zeitplan
            # aus, plant der naechste proaktive Lauf im Abendfenster trotzdem.
            prompt = prompt + "\n" + plan_rhythm.rhythm_note(_agent, now)
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

        # Final atomic busy-check immediately before dispatch (#548): the earlier
        # check above (line ~614) only ever covered [Proactive] schedules and
        # even then wasn't atomic — a second scheduler tick could read
        # queue_depth=0 in the window between our read and our push and
        # dispatch too. This one covers EVERY schedule type (incl. [Plan]
        # blocks, which previously had no guard at all) and is race-free: the
        # re-check and the push both happen while holding a per-agent lock, so
        # no other dispatch can slip in between.
        lock_token = None
        if schedule.agent_id:
            lock_token = await self.redis.acquire_dispatch_lock(schedule.agent_id)
            if lock_token is None:
                # Another dispatch for this agent is in flight right now — resolves
                # in seconds, so retry on roughly the next tick instead of losing
                # the whole day over a momentary collision.
                schedule.next_run_at = await self._retry_or_advance(
                    schedule, now, reason="lock",
                    max_attempts=_LOCK_RETRY_MAX_ATTEMPTS, delay=_LOCK_RETRY_DELAY,
                )
                logger.info(
                    "[Scheduler] %s skipped - dispatch lock held for agent %s",
                    schedule.name, schedule.agent_id,
                )
                return
            queue_depth = await self.redis.get_queue_depth(schedule.agent_id)
            status = await self.redis.get_agent_status(schedule.agent_id)
            current_task = status.get("current_task", "")
            is_busy_with_task = queue_depth > 0 or (
                current_task and not current_task.startswith("chat:")
            )
            if is_busy_with_task:
                await self.redis.release_dispatch_lock(schedule.agent_id, lock_token)
                schedule.next_run_at = await self._retry_or_advance(
                    schedule, now, reason="busy",
                    max_attempts=_TRANSIENT_RETRY_MAX_ATTEMPTS, delay=_TRANSIENT_RETRY_DELAY,
                )
                logger.info(
                    "[Scheduler] %s skipped - agent busy (queue=%s, task=%r)",
                    schedule.name, queue_depth, current_task,
                )
                return

        try:
            task = await router.create_and_route_task(
                title=f"[Scheduled] {schedule.name}",
                prompt=prompt,
                priority=schedule.priority,
                agent_id=schedule.agent_id,
                model=schedule.model,
                metadata={"schedule_id": schedule.id},
            )
        finally:
            if lock_token:
                await self.redis.release_dispatch_lock(schedule.agent_id, lock_token)

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
        await self._clear_retry_budgets(schedule)
        if not schedule.cron_expression and schedule.interval_seconds == 0:
            schedule.enabled = False
            schedule.next_run_at = now
        else:
            schedule.next_run_at = _calc_next_run(schedule, now)

        logger.info(
            "[Scheduler] %s triggered task %s, next run at %s",
            schedule.name, task.id, schedule.next_run_at.isoformat(),
        )

    async def _next_run_after_overload(self, schedule: Schedule, now: datetime) -> datetime:
        """Retry briefly for transient overload before giving up on the slot."""
        return await self._retry_or_advance(
            schedule, now, reason="overload",
            max_attempts=_OVERLOAD_RETRY_MAX_ATTEMPTS, delay=_OVERLOAD_RETRY_DELAY,
        )

    async def _retry_or_advance(
        self, schedule: Schedule, now: datetime, *, reason: str,
        max_attempts: int, delay: timedelta,
    ) -> datetime:
        """Retry briefly for a transient skip before giving up on today's slot.

        Generalizes the overload-retry fix (#605/v1.220.4) to every other
        transient reason a due schedule gets skipped for (agent briefly down,
        momentarily busy, a dispatch lock held for a few seconds). Before this,
        a single collision at the exact cron tick jumped straight to
        _calc_next_run(now) — for a once-a-day schedule that meant losing the
        whole day for a blip that may have cleared a minute later. One Redis
        counter per (reason, schedule) so different reasons don't share a
        retry budget.
        """
        client = getattr(self.redis, "client", None)
        if client is None:
            return _calc_next_run(schedule, now)

        key = f"schedule:retry:{reason}:{schedule.id}"
        slot_key = f"{key}:slot"
        slot = as_utc(schedule.next_run_at)
        try:
            attempt = int(await client.incr(key))
            if attempt == 1:
                await client.expire(key, _OVERLOAD_RETRY_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.debug("[Scheduler] Retry-Zaehler (%s) nicht verfuegbar", reason, exc_info=True)
            return _calc_next_run(schedule, now)

        # Den urspruenglichen Soll-Slot merken, sonst meldet das Aufgeben spaeter
        # die letzte Wiederholung statt der Uhrzeit, die im Zeitplan steht. Eigenes
        # try: eine Stoerung hier darf keine Wiederholung in ein Aufgeben verwandeln.
        try:
            await client.set(
                slot_key, slot.isoformat(), ex=_OVERLOAD_RETRY_TTL_SECONDS, nx=True,
            )
            if attempt > max_attempts:
                stored = await client.get(slot_key)
                if stored:
                    slot = as_utc(datetime.fromisoformat(stored))
                await client.delete(key, slot_key)
        except Exception:  # noqa: BLE001
            logger.debug("[Scheduler] Soll-Slot (%s) nicht verfuegbar", reason, exc_info=True)

        if attempt <= max_attempts:
            return now + delay
        await self._report_dropped_slot(schedule, slot, reason=reason, attempts=max_attempts)
        return _calc_next_run(schedule, now)

    async def _clear_retry_budgets(self, schedule: Schedule) -> None:
        """Wiederholungs-Budgets nach einem geglueckten Lauf zuruecksetzen.

        Die Zaehler leben 6 Stunden. Ohne Ruecksetzen erbt der naechste Slot
        eines stuendlichen Zeitplans das schon aufgebrauchte Budget des
        vorherigen — er wird beim ersten Huerdchen sofort verworfen, und die
        Meldung nennt den alten, laengst gelaufenen Soll-Slot.
        """
        client = getattr(self.redis, "client", None)
        if client is None:
            return
        keys = [f"schedule:retry:{r}:{schedule.id}" for r in _RETRY_REASONS]
        try:
            await client.delete(*keys, *(f"{k}:slot" for k in keys))
        except Exception:  # noqa: BLE001
            logger.debug("[Scheduler] Retry-Budgets nicht ruecksetzbar", exc_info=True)

    async def _report_dropped_slot(
        self, schedule: Schedule, slot: datetime, *, reason: str, attempts: int,
    ) -> None:
        """Einen endgueltig verworfenen Lauf zaehlen und melden (#631).

        Bis hierher war das Aufgeben die einzige Zustandsaenderung im Scheduler,
        die weder gezaehlt noch gemeldet wurde: der Skip-Zweig kehrt vor
        ``total_runs += 1`` zurueck, und ``_retry_or_advance`` setzt
        ``next_run_at`` in die Zukunft. Damit sah der Fehler-Waechter
        ``drift == 0``, der Verpasst-Waechter fand nichts (er sucht
        ``next_run_at`` in der Vergangenheit), und ``success_rate`` blieb 1.0 —
        ein Tageszeitplan konnte tagelang ausfallen und meldete perfekte Quote.
        ``last_run_at`` bleibt bewusst unberuehrt: es hat kein Lauf
        stattgefunden.
        """
        import json as _json

        # Einmal-Laeufe (Plan-Bloecke) verlieren nichts: sie behalten ihren
        # einen Auftrag und versuchen es in 60 Sekunden wieder. Nur wer eine
        # feste Wiederkehr hat, verliert wirklich den Termin von heute.
        if not schedule.cron_expression and not schedule.interval_seconds:
            return

        schedule.total_runs += 1
        schedule.fail_count += 1

        logger.warning(
            "[Scheduler] %s: Slot %s nach %s Versuchen (%s) verworfen",
            schedule.name, slot.isoformat(), attempts, reason,
        )

        client = getattr(self.redis, "client", None)
        if client is None:
            return
        try:
            fresh = await client.set(
                f"schedule:dropped:{schedule.id}", "1",
                nx=True, ex=_DROPPED_SLOT_ALERT_COOLDOWN_SECONDS,
            )
        except Exception:  # noqa: BLE001
            logger.debug("[Scheduler] Meldungs-Drossel nicht verfuegbar", exc_info=True)
            fresh = True
        if not fresh:
            return

        payload = {
            "text": (
                f"Zeitplan *{md_escape(schedule.name)}*: Lauf um "
                f"{slot.isoformat()} entfaellt ersatzlos "
                f"(Grund: {md_escape(reason)}, nach {attempts} Versuchen). "
                f"Naechster regulaerer Termin unveraendert."
            ),
            "parse_mode": "Markdown",
        }
        try:
            await client.publish("telegram:notification", _json.dumps(payload))
        except Exception as e:  # noqa: BLE001
            logger.warning("[Scheduler] DroppedSlot publish error: %s", e)

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

        from app.core.day_plan_store import block_prompt
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
                # last_run_at statt total_runs: ein verworfener Slot (#631) zaehlt
                # als fehlgeschlagener Lauf mit, hat aber nichts ausgefuehrt — ueber
                # total_runs wuerde der Block als erledigt abgehakt und per
                # Titel-Suche an einen fremden Task gehaengt.
                .where(AgentPlanItem.status == "planned", Schedule.last_run_at.is_not(None))
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

            # Arming orphans (SELECT unclaimed items, INSERT a Schedule each) is not
            # atomic by itself: two scheduler ticks — from this process or another
            # orchestrator replica — can both SELECT the same "schedule_id IS NULL"
            # item before either COMMITs, and each create its OWN Schedule row for
            # it. That is exactly what happened for #548 on 2026-08-13: one plan
            # block ("Deploy-Gate Status pruefen") got two independent Schedule
            # rows, each later fired its own [Plan] task, and the two tasks sent
            # contradicting Telegram updates to the user. The per-agent dispatch
            # lock above doesn't help here — it guards *dispatch*, this races
            # earlier, at *schedule creation*. Hold a short-lived global lock
            # around select+insert so only one process can arm orphans at a time;
            # by the time a second process gets the lock, the first has already
            # committed, so the item no longer shows up as an orphan.
            lock_token = await self.redis.acquire_lock("arm_plan_blocks", ttl_seconds=25)
            if lock_token is None:
                return armed
            try:
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
                        prompt=block_prompt(item),
                        interval_seconds=0,
                        priority=0 if item.priority == "high" else 1,
                        agent_id=item.agent_id,
                        enabled=True,
                        # Verpasste Zeiten werden nachgeholt — aber GESTAFFELT. Fuenf
                        # Bloecke, deren Zeit vorbei ist, wuerden sonst gleichzeitig
                        # feuern; auf einem Pi bringt das die CLI zum Absturz (exit -6).
                        next_run_at=max(
                            item.planned_start,
                            datetime.now(timezone.utc) + timedelta(minutes=3 * armed),
                        ) if item.planned_start < datetime.now(timezone.utc)
                        else item.planned_start,
                    ))
                    item.schedule_id = schedule_id
                    armed += 1
                if armed:
                    # Ohne das faellt beim Verlassen der Sitzung alles weg: die Zeitplaene
                    # waren angelegt und beim naechsten Blick wieder verschwunden.
                    await db.commit()
            finally:
                await self.redis.release_lock("arm_plan_blocks", lock_token)
        return armed

    async def _night_runs(
        self, db: AsyncSession, agent_id: str | None, now: datetime,
    ) -> list[dict]:
        """Die Laeufe seit der letzten Planung — Titel und Ausgang, mehr nicht.

        Genau das, was ein Mensch morgens als Erstes anschaut: Was lief durch, was ist
        auf die Nase gefallen? Ohne diese Liste plant der Morgencheck an der Nacht vorbei.
        """
        if not agent_id:
            return []
        try:
            rows = (await db.execute(
                select(Task)
                .where(Task.agent_id == agent_id,
                       Task.created_at >= now - timedelta(hours=14))
                .order_by(Task.created_at.desc()).limit(25)
            )).scalars().all()
        except Exception:  # noqa: BLE001 — ohne die Liste plant er eben ohne sie
            logger.debug("[Rhythmus] Nachtlaeufe nicht lesbar", exc_info=True)
            return []
        out: list[dict] = []
        for row in rows:
            state = str(getattr(row.status, "value", row.status)).lower()
            if state in ("pending", "queued"):
                continue          # noch nicht gelaufen — sagt ueber die Nacht nichts aus
            out.append({"title": row.title or "", "status": state})
        return out

    async def _ensure_planning_rhythm(self) -> int:
        """Jeder proaktive Agent plant abends und schaut morgens drueber. Invariante.

        Ein Agent hatte diesen Rhythmus — er hatte ihn sich im Chat selbst eingerichtet.
        Alle anderen planten irgendwann mitten am Tag oder gar nicht, und der Montag
        blieb leer, weil sonntags niemand plante. Deshalb steht der Rhythmus hier und
        nicht in einer Anleitung: Wer einen aktiven ``[Proactive]``-Zeitplan hat, bekommt
        die zwei Rhythmus-Zeitplaene dazu — ueber dieselbe Maschinerie, kein Sonderweg.

        Bestehende Rhythmus-Zeitplaene werden nur an die Uhrzeit angepasst, wenn der
        Nutzer die Dienstzeit geaendert hat. Ausgeschaltet lassen kann er sie: ein
        ``enabled=False`` wird respektiert und nicht wieder angeknipst.
        """
        from sqlalchemy import or_

        from app.core import plan_rhythm
        from app.models.agent import Agent as _Agent

        created = 0
        async with resilient_session() as db:
            agent_ids = (await db.execute(
                select(Schedule.agent_id).where(
                    Schedule.name.startswith("[Proactive]"),
                    Schedule.enabled.is_(True),
                    Schedule.agent_id.isnot(None),
                ).distinct()
            )).scalars().all()
            if not agent_ids:
                return 0
            existing = (await db.execute(
                select(Schedule).where(
                    Schedule.agent_id.in_(agent_ids),
                    Schedule.name.startswith(plan_rhythm.SCHEDULE_PREFIX),
                )
            )).scalars().all()
            by_agent: dict[tuple[str, str], Schedule] = {
                (s.agent_id, s.name): s for s in existing
            }
            agents = {a.id: a for a in (await db.execute(
                select(_Agent).where(_Agent.id.in_(agent_ids))
            )).scalars().all()}
            # Alt-Bestand: „Tagesplanung am Morgen" legte frueher einen EIGENEN
            # Zeitplan an. Der Morgencheck macht dasselbe — zwei Planungslaeufe an
            # einem Morgen sind einer zu viel, also raeumt der Abgleich ihn weg.
            # Zwei Namensschemata aus der Vor-Rhythmus-Zeit: das juengere endet auf
            # „— Tagesplanung", ein aelteres nutzte „[Plan] Morgencheck:"/
            # „[Plan] Abendplanung:" (mit Datum im Titel, deshalb Praefix-Vergleich
            # bis zum Doppelpunkt statt exaktem Namen) — beide blieben bislang
            # unentdeckt liegen und feuerten fuer einen gestoppten Agenten seither
            # alle 30 Sekunden ins Leere.
            legacy = (await db.execute(
                select(Schedule).where(
                    Schedule.agent_id.in_(agent_ids),
                    or_(
                        Schedule.name.like("%— Tagesplanung"),
                        Schedule.name.like("[Plan] Morgencheck:%"),
                        Schedule.name.like("[Plan] Abendplanung:%"),
                    ),
                )
            )).scalars().all()
            for old in legacy:
                logger.info("[Rhythmus] Alten Planungslauf %s (%s) entfernt — der "
                            "Morgencheck uebernimmt", old.id, old.name)
                await db.delete(old)
            now = datetime.now(timezone.utc)
            for agent_id in agent_ids:
                agent = agents.get(agent_id)
                if agent is None:
                    continue
                crons = plan_rhythm.cron_expressions(agent)
                for name, cron in (
                    (plan_rhythm.EVENING_SCHEDULE_NAME, crons["evening"]),
                    (plan_rhythm.MORNING_SCHEDULE_NAME, crons["morning"]),
                ):
                    found = by_agent.get((agent_id, name))
                    if found is not None:
                        if (found.cron_expression != cron
                                or found.timezone != crons["timezone"]):
                            found.cron_expression = cron
                            found.timezone = crons["timezone"]
                            found.next_run_at = _calc_next_run(found, now)
                            logger.info("[Rhythmus] %s fuer %s auf %s (%s) gesetzt",
                                        name, agent_id, cron, crons["timezone"])
                        continue
                    sched = Schedule(
                        id=uuid.uuid4().hex[:8],
                        name=name,
                        # Der Text wird beim Feuern aus dem Code gebaut (siehe
                        # _execute_schedule) — hier steht nur, was in der UI lesbar ist.
                        prompt=(
                            "Wird beim Ausführen aus dem Code gebaut: "
                            "Tagesplanung am Abend bzw. Durchsicht am Morgen."
                        ),
                        interval_seconds=0,
                        cron_expression=cron,
                        timezone=crons["timezone"],
                        priority=0,
                        agent_id=agent_id,
                        enabled=True,
                        next_run_at=now,
                    )
                    sched.next_run_at = _calc_next_run(sched, now)
                    db.add(sched)
                    created += 1
                    logger.info("[Rhythmus] %s fuer %s angelegt (%s, %s)",
                                name, agent_id, cron, crons["timezone"])
            await db.commit()
        return created

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

    async def _dispatch_due_retries(self) -> int:
        """Faellige Wiederholungen der Selbstheilung abschicken (#390).

        Die Entscheidung, OB und WANN wiederholt wird, faellt im Task-Router beim
        Fehlschlag — hier wird nur noch abgeschickt, was faellig geworden ist. So
        gibt es genau eine Stelle mit der Regel und genau eine mit der Uhr.
        """
        async with resilient_session() as db:
            lb = LoadBalancer(self.redis)
            router = TaskRouter(db, self.redis, lb, docker_service=self.docker)
            return await router.dispatch_due_retries()

    async def _tick_stale_task_watchdog(self) -> None:
        """Mark RUNNING tasks that stopped heart-beating as stale.

        A worker that crashes mid-job (container OOM, network drop) leaves its
        task pinned in RUNNING forever. updated_at stops advancing, so we flip
        such tasks to FAILED with a `stale` metadata flag and alert the owner —
        instead of the operator discovering a missing artifact hours later.
        """
        import json as _json
        from datetime import timedelta as _td

        from app.config import settings as _cfg

        # Einstellbar seit #692: der feste 30-Minuten-Wert war faktisch eine
        # Obergrenze fuer jede delegierte Aufgabe, weil niemand ein Lebenszeichen
        # sendete. Der Herzschlag kommt jetzt — aber ein Agent auf einem aelteren
        # Abbild sendet ihn noch nicht, deshalb liegt der Standard hoeher.
        schwelle = _td(minutes=max(1, int(getattr(_cfg, "watchdog_stale_task_minutes", 180))))
        now = datetime.now(timezone.utc)
        async with resilient_session() as db:
            stale = await find_stale_tasks(db, now, schwelle)
            if not stale:
                return
            from app.models.notification import Notification

            minuten = int(schwelle.total_seconds() // 60)
            for task in stale:
                mark_task_stale(task, now, schwelle)
                # Ohne das laeuft der Agent nach dem Abbruch weiter und verbrennt
                # Zeit und Token fuer ein Ergebnis, das niemand mehr annimmt
                # (#692 Punkt C). Der Kanal existiert bereits fuer `cancel_task`.
                if self.redis and self.redis.client and task.agent_id:
                    try:
                        # Rohe Kennung, genau wie `cancel_task` — der Zuhoerer im
                        # Agenten liest die Nutzlast als ID, JSON wuerde er fuer
                        # eine unbekannte Aufgabe halten und nichts stoppen.
                        await self.redis.client.publish(
                            f"agent:{task.agent_id}:task:cancel", task.id
                        )
                    except Exception as e:
                        logger.warning("[Scheduler] StaleTaskWatchdog cancel error: %s", e)
                db.add(
                    Notification(
                        agent_id=task.agent_id or "system",
                        type="error",
                        title="Task stale (kein Heartbeat)",
                        message=(
                            f'Task "{task.title}" hat seit über {minuten} min kein '
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
                            f"Heartbeat >{minuten} min (id `{task.id}`), als fehlgeschlagen markiert."
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

    async def _tick_sentinel_liveness(self) -> None:
        """Meldet, wenn der Sentinel verstummt ist (#590 Punkt 6).

        Ein Waechter, der unbemerkt stehenbleibt, ist gefaehrlicher als gar
        keiner: die Anlage sieht ueberwacht aus und ist es nicht. Deshalb
        ueberwacht der Wachhund den Waechter.

        Ein FEHLENDES Lebenszeichen ist kein Alarm — dann ist der Dienst schlicht
        aus, und das ist ein bewusster Zustand. Gemeldet wird nur, wer einmal
        gelebt hat und dann verstummt.
        """
        from app.models.notification import Notification
        from app.services.sentinel_service import SENTINEL_HEARTBEAT_KEY

        if not self.redis or not self.redis.client:
            return
        try:
            schlag = await self.redis.client.get(SENTINEL_HEARTBEAT_KEY)
        except Exception:  # noqa: BLE001 — Redis weg ist ein anderes Problem
            return
        if isinstance(schlag, bytes):
            schlag = schlag.decode()

        now = datetime.now(timezone.utc)
        if not is_sentinel_stale(schlag, now):
            self._sentinel_alerted = False
            return
        if self._sentinel_alerted:
            return          # einmal melden, nicht alle 30 Sekunden
        self._sentinel_alerted = True
        logger.error("[Scheduler] Sentinel verstummt — letztes Lebenszeichen: %s", schlag)
        async with resilient_session() as db:
            db.add(Notification(
                agent_id="system",
                type="error",
                title="Sentinel antwortet nicht mehr",
                message=(
                    "Die Verhaltensueberwachung hat sich seit ueber zwei Minuten "
                    "nicht gemeldet. Sie laeuft also nicht mehr, waehrend die "
                    "Oberflaeche sie als aktiv fuehrt — Agenten laufen derzeit "
                    "unbeaufsichtigt. Orchestrator-Protokoll pruefen."
                ),
                priority="urgent",
            ))
            await db.commit()

    async def _alert_due_schedules_down(self, streak: int) -> None:
        """Escalate once a DB outage has blocked schedule-checking for a while.

        A single failed tick is a harmless blip and self-heals on its own —
        see _TRANSIENT_DB_ERRORS above. But if the DB stays unreachable for
        minutes, NO schedule can fire during that window (the 06:00 jobs
        included), and until now nothing told the user unless a schedule
        happened to have its own separate safety-net job. Root-caused via
        issue #601 on 2026-08-15.
        """
        outage_min = round(streak * 30 / 60, 1)
        logger.error(
            "[Scheduler] DueSchedules DB unreachable for %s consecutive ticks "
            "(~%s min) — schedules may be missed", streak, outage_min,
        )
        try:
            from app.models.notification import Notification
            async with resilient_session() as db:
                db.add(Notification(
                    agent_id="system",
                    type="error",
                    title="Zeitplaene koennen nicht geprueft werden",
                    message=(
                        f"Die Datenbank ist seit ~{outage_min} Minuten nicht "
                        "erreichbar, waehrend der Scheduler faellige Zeitplaene "
                        "pruefen wollte. Faellige Jobs feuern in diesem Fenster "
                        "nicht von selbst nach — nur ein eigens eingerichteter "
                        "Safety-Net-Zeitplan wuerde sie nachtraeglich abfangen."
                    ),
                    priority="urgent",
                ))
                await db.commit()
        except _TRANSIENT_DB_ERRORS as e:
            # DB still down — the Notification row can't be written either, but
            # the Telegram publish below goes over Redis, not the DB, so it can
            # still reach the user.
            logger.warning("[Scheduler] DueSchedules alert Notification write failed (DB still down): %s", e)
        if self.redis and self.redis.client:
            import json as _json
            payload = {
                "text": (
                    f"🔴 Scheduler: Datenbank seit ~{outage_min} Minuten nicht "
                    "erreichbar — faellige Zeitplaene werden gerade nicht geprueft."
                ),
                "parse_mode": "Markdown",
            }
            try:
                await self.redis.client.publish("telegram:notification", _json.dumps(payload))
            except Exception as e:
                logger.warning("[Scheduler] DueSchedules alert publish error: %s", e)

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
                logger.warning("[Scheduler] Unknown timezone '%s' — evaluating cron in UTC", scrub_log(tz_name))
                tz = timezone.utc
            base = now.astimezone(tz)
            cron = croniter(schedule.cron_expression, base)
            return cron.get_next(datetime).astimezone(timezone.utc)
        except Exception as e:
            logger.warning(
                "[Scheduler] Invalid cron expression '%s': %s — falling back to interval",
                scrub_log(schedule.cron_expression), scrub_log(e),
            )
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
                scrub_log(schedule.cron_expression), scrub_log(e),
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
