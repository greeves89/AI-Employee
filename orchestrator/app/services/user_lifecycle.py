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
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log_redaction import scrub_log
from app.db.session import resilient_session
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

# Connect-level DB errors that resilient_session already retried and
# exhausted during a brief DB blip. Dieselbe Klasse wie
# scheduler_service._TRANSIENT_DB_ERRORS.
_TRANSIENT_DB_ERRORS = (OperationalError, DBAPIError, ConnectionError, TimeoutError)

# Consecutive failed sweeps (60s cadence) before escalating to the user.
# 2 ticks (~2min), nicht 1: ein einzelner Ausrutscher heilt lautlos selbst
# und ist keinen Alarm wert (#617): "app.services.user_lifecycle: 130 ERROR
# (Sweep error)" ueber Stunden ohne jede Eskalation war der gemeldete Zustand
# — derselbe Fehlerklasse, fuer die _check_due_schedules im Scheduler bereits
# eskaliert (#601/#719).
_SWEEP_ALERT_THRESHOLD = 2


async def _has_imminent_schedule(db: AsyncSession, agent_id: str, now: datetime,
                                 minutes: int) -> bool:
    """Faellt fuer den Agenten binnen ``minutes`` ein aktiver Zeitplan an?"""
    try:
        from app.models.schedule import Schedule
        horizon = now + timedelta(minutes=minutes)
        hit = await db.scalar(
            select(Schedule.id).where(
                Schedule.agent_id == agent_id,
                Schedule.enabled.is_(True),
                Schedule.next_run_at <= horizon,
            ).limit(1)
        )
        return hit is not None
    except Exception:  # noqa: BLE001 — im Zweifel lieber wach lassen
        logger.debug("[UserLifecycle] Zeitplan-Pruefung fehlgeschlagen", exc_info=True)
        return True


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
        # Consecutive failed sweeps + wall-clock time of the first failed
        # tick in the current outage (reset on the first successful sweep).
        # Wanduhrzeit statt streak*60s (#719-Lehre): auch hier wartet
        # resilient_session seinen eigenen Timeout aus, ein Tick kann also
        # laenger als 60s dauern.
        self._sweep_fail_streak = 0
        self._sweep_first_fail_at: datetime | None = None
        self._sweep_next_alert_streak = _SWEEP_ALERT_THRESHOLD

    async def run(self) -> None:
        """Main loop: every minute, check for inactive users and stop their agents."""
        self._running = True
        logger.info("[UserLifecycle] Started")
        while self._running:
            try:
                await self._sweep()
                if self._sweep_fail_streak >= _SWEEP_ALERT_THRESHOLD:
                    logger.info(
                        "[UserLifecycle] Sweep DB recovered after %s failed tick(s)",
                        self._sweep_fail_streak,
                    )
                self._sweep_fail_streak = 0
                self._sweep_first_fail_at = None
                self._sweep_next_alert_streak = _SWEEP_ALERT_THRESHOLD
            except _TRANSIENT_DB_ERRORS as e:
                self._sweep_fail_streak += 1
                if self._sweep_first_fail_at is None:
                    self._sweep_first_fail_at = datetime.now(timezone.utc)
                logger.warning(
                    "[UserLifecycle] Sweep DB unavailable (transient, retrying "
                    "next tick, %s consecutive): %s: %s",
                    self._sweep_fail_streak, type(e).__name__, e,
                )
                if self._sweep_fail_streak >= self._sweep_next_alert_streak:
                    self._sweep_next_alert_streak = self._sweep_fail_streak * 2
                    try:
                        await self._alert_sweep_down(
                            self._sweep_fail_streak, self._sweep_first_fail_at,
                        )
                    except Exception as alert_err:
                        logger.warning(
                            "[UserLifecycle] Sweep alert error: %s", alert_err,
                        )
            except Exception as e:
                logger.error("[UserLifecycle] Sweep error: %s", e, exc_info=True)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    async def _alert_sweep_down(
        self, streak: int, first_fail_at: datetime | None = None,
    ) -> None:
        """Escalate while a DB outage keeps blocking the idle-agent sweep.

        Spiegelt SchedulerService._alert_due_schedules_down (#601/#719): ohne
        das lief eine Datenbank-Stoerung hier bislang nur als wiederholtes
        ERROR-Log durch, ohne dass jemand benachrichtigt wurde (#617) —
        waehrenddessen laufen ungenutzte Agenten-Container einfach weiter,
        statt nach Ablauf ihrer Leerlaufzeit gestoppt zu werden.
        """
        if first_fail_at is not None:
            outage_min = round((datetime.now(timezone.utc) - first_fail_at).total_seconds() / 60, 1)
        else:
            outage_min = round(streak * CHECK_INTERVAL_SECONDS / 60, 1)
        logger.error(
            "[UserLifecycle] Sweep DB unreachable for %s consecutive ticks "
            "(~%s min) — idle agents may keep running unattended", streak, outage_min,
        )
        try:
            from app.models.notification import Notification
            async with resilient_session(session_factory=self.db_factory) as db:
                db.add(Notification(
                    agent_id="system",
                    type="error",
                    title="Leerlauf-Ueberwachung kann Agenten nicht pruefen",
                    message=(
                        f"Die Datenbank ist seit ~{outage_min} Minuten nicht "
                        "erreichbar, waehrend geprueft werden sollte, welche "
                        "Agenten wegen Inaktivitaet gestoppt werden koennen. "
                        "Bis die Datenbank wieder erreichbar ist, laufen "
                        "untaetige Agenten-Container unbeaufsichtigt weiter."
                    ),
                    priority="urgent",
                ))
                await db.commit()
        except _TRANSIENT_DB_ERRORS as e:
            logger.warning(
                "[UserLifecycle] Sweep alert Notification write failed (DB still down): %s", e,
            )
        if self.redis and self.redis.client:
            import json as _json
            payload = {
                "text": (
                    f"🔴 Leerlauf-Ueberwachung: Datenbank seit ~{outage_min} "
                    "Minuten nicht erreichbar — untaetige Agenten werden gerade "
                    "nicht gestoppt."
                ),
                "parse_mode": "Markdown",
            }
            try:
                await self.redis.client.publish("telegram:notification", _json.dumps(payload))
            except Exception as e:
                logger.warning("[UserLifecycle] Sweep alert publish error: %s", e)

    async def _sweep(self) -> None:
        """One sweep: find inactive users and stop their idle agents."""
        from app.db.session import resilient_session
        async with resilient_session(session_factory=self.db_factory) as db:
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

                # Always-on agents are exempt from ALL idle reaping (independent of the
                # owner's activity). Single flag honored by both idle sweeps.
                if (agent.config or {}).get("always_on"):
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

                # Steht in Kuerze ein Zeitplan oder Kalender-Block an, lohnt das
                # Schlafenlegen nicht — der Scheduler muesste den Agenten sofort
                # wieder kalt starten (#632).
                if await _has_imminent_schedule(db, agent.id, now, minutes=max(timeout, 10)):
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
                logger.warning(f"[UserLifecycle] start_container failed for {scrub_log(agent_id)}: {scrub_log(e)}")

    # Container missing or start failed — recreate via AgentManager
    logger.info(f"[UserLifecycle] Container gone for {scrub_log(agent_id)}, recreating via AgentManager")
    from app.core.agent_manager import AgentManager
    from app.services.redis_service import RedisService
    from app.config import settings

    # Der Neuaufbau braucht eine VERBUNDENE Redis-Verbindung: Bei
    # eingeschaltetem ``redis_acl_enabled`` holt sich der Agenten-Verwalter
    # fuer jeden Container einen eigenen Redis-Zugang
    # (``_agent_redis_url`` -> ``ensure_agent_acl_user``). Ohne ``connect()``
    # scheitert genau das mit "Redis not connected" — und ein Agent, dessen
    # Container verschwunden ist, kommt nie wieder. Dieser Pfad laeuft im
    # Minutentakt, deshalb wird die Verbindung hinterher wieder geschlossen.
    redis_service = RedisService(settings.redis_url)
    try:
        await redis_service.connect()
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
        logger.warning(f"[UserLifecycle] Could not recreate agent {scrub_log(agent_id)}: {scrub_log(e)}")
        return False
    finally:
        try:
            await redis_service.disconnect()
        except Exception:  # noqa: BLE001 — Aufraeumen darf den Weckpfad nie kippen
            logger.debug("[UserLifecycle] Redis-Verbindung liess sich nicht schliessen", exc_info=True)
