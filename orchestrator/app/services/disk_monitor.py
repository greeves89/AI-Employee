"""Disk quota monitor for agent workspaces.

Runs every 5 minutes, checks /workspace usage per agent against the configured
soft quota (agent_workspace_size_gb). Writes a warning file the agent can read,
and stops the agent if usage exceeds 95 % of the quota.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.models.agent import Agent, AgentState

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 300  # 5 minutes
_WARN_THRESHOLD = 80.0
_STOP_THRESHOLD = 95.0
# Gleiche Aufbewahrungsfrist wie der normale Abschlusspfad
# (task_router.TASK_EVICT_GRACE_SECONDS) — hier nicht importiert, um den
# Monitor nicht an den Router zu koppeln.
_TASK_EVICT_GRACE = timedelta(days=7)


class DiskMonitorService:
    def __init__(self, session_factory, docker_service, redis=None) -> None:
        self._sf = session_factory
        self.docker = docker_service
        self._redis = redis
        self._running = True

    async def run(self) -> None:
        await asyncio.sleep(60)  # brief startup delay
        while self._running:
            try:
                await self._check_all_agents()
            except Exception as exc:
                logger.error("Disk monitor cycle failed: %s", exc, exc_info=True)
            await asyncio.sleep(_CHECK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    async def _check_all_agents(self) -> None:
        from app.db.session import resilient_session
        async with resilient_session(session_factory=self._sf) as db:
            result = await db.execute(
                select(Agent).where(
                    Agent.state.in_([AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING])
                )
            )
            agents = list(result.scalars().all())

        loop = asyncio.get_running_loop()
        for agent in agents:
            if not agent.container_id:
                continue
            try:
                # Per-agent override takes precedence over global default
                limit_gb = float(agent.config.get("workspace_size_gb") or settings.agent_workspace_size_gb) if agent.config else settings.agent_workspace_size_gb
                stats = await loop.run_in_executor(
                    None,
                    self.docker.get_workspace_disk_usage,
                    agent.container_id,
                    limit_gb,
                )
                if not stats:
                    continue

                percent = stats["disk_percent"]
                logger.debug(
                    "Agent %s workspace: %.1f%% (%.0f / %.0f MB)",
                    agent.id,
                    percent,
                    stats["disk_usage_mb"],
                    stats["disk_limit_mb"],
                )

                if percent >= _STOP_THRESHOLD:
                    await self._stop_agent(agent, stats)
                elif percent >= _WARN_THRESHOLD:
                    await loop.run_in_executor(None, self._write_warning, agent.container_id, stats)
                else:
                    await loop.run_in_executor(None, self._clear_warning, agent.container_id)

            except Exception as exc:
                logger.warning("Disk check failed for agent %s: %s", agent.id, exc)

    def _write_warning(self, container_id: str, stats: dict) -> None:
        """Write /workspace/.disk_warning so the agent sees it on its next file check."""
        avail = stats["disk_available_mb"]
        avail_str = f"{avail:.0f} MB" if avail >= 1 else f"{avail * 1024:.0f} KB"
        content = (
            f"DISK WARNING: {stats['disk_percent']:.1f}% of workspace quota used\n"
            f"Used:      {stats['disk_usage_mb']:.0f} MB / {stats['disk_limit_mb']:.0f} MB\n"
            f"Available: {avail_str}\n\n"
            f"Please clean up before you run out of space:\n"
            f"  rm -rf /workspace/data/cache /workspace/tmp\n"
            f"  find /workspace -name '*.log' -delete\n"
            f"  du -sh /workspace/* | sort -rh | head -10\n"
        )
        self.docker.write_file_in_container(container_id, "/workspace/.disk_warning", content)

    def _clear_warning(self, container_id: str) -> None:
        try:
            self.docker.exec_in_container(container_id, "rm -f /workspace/.disk_warning")
        except Exception:
            pass

    async def _stop_agent(self, agent: Agent, stats: dict) -> None:
        logger.warning(
            "Agent %s (%s) exceeded %.0f%% disk quota (%.0f / %.0f MB) — stopping container",
            agent.id,
            agent.name,
            _STOP_THRESHOLD,
            stats["disk_usage_mb"],
            stats["disk_limit_mb"],
        )
        try:
            # Write a final warning before stopping so the user sees why
            self._write_warning(agent.container_id, stats)
            # Vor dem Stopp: laufende Aufgaben explizit als fehlgeschlagen
            # verbuchen und den Betreiber alarmieren (#714). Der Container wird
            # gleich unter ihnen weggezogen; ohne das hier verbucht der
            # normale Abschlusspfad das dann noch Verfuegbare (leer/mitten im
            # Satz abgeschnitten) als "completed" — ein toter Agent meldet
            # damit erledigte Arbeit, die nie fertig wurde.
            await self._fail_running_tasks_and_alert(agent, stats)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.docker.stop_container, agent.container_id)
            await self._cleanup_and_maybe_restart(agent, stats)
        except Exception as exc:
            logger.error("Failed to stop disk-full agent %s: %s", agent.id, exc)

    async def _cleanup_and_maybe_restart(self, agent: Agent, stats: dict) -> None:
        """Raeumt das Volume des gestoppten Agenten auf und startet ihn neu,
        wenn das reicht (Issue #714, Punkt 1 — die eigentliche Verklemmung).

        Vorher gab es keinen Weg zurueck: Aufraeumen setzte einen laufenden
        Behaelter voraus, und genau den verhinderte der Zustand, der
        aufgeraeumt werden musste. Ein kurzlebiger Helfer-Container, der NUR
        das Volume mountet (gleiches Muster wie ``copy_workspace_volume``),
        braucht den Agenten-Container selbst nicht — Aufraeumen ist also auch
        im gestoppten Zustand moeglich. Reicht es, startet der Agent
        automatisch neu; reicht es nicht, bleibt er gestoppt (der Betreiber
        wurde bereits ueber ``_fail_running_tasks_and_alert`` alarmiert und
        raeumt danach manuell auf, wie der Alarmtext es vorschlaegt).
        """
        if not agent.volume_name:
            logger.warning(
                "Agent %s hat kein volume_name verzeichnet — Aufraeumlauf uebersprungen",
                agent.id,
            )
            return
        loop = asyncio.get_running_loop()
        used_mb = await loop.run_in_executor(
            None, self.docker.cleanup_workspace_volume, agent.volume_name
        )
        if used_mb is None:
            return
        limit_mb = stats["disk_limit_mb"]
        percent = round(min(used_mb / limit_mb * 100, 100), 2) if limit_mb else 100.0
        if percent >= _STOP_THRESHOLD:
            logger.warning(
                "Agent %s: Aufraeumlauf senkte Belegung nur auf %.1f%% (weiterhin >= %.0f%%) — "
                "bleibt gestoppt, manuelles Aufraeumen noetig",
                agent.id, percent, _STOP_THRESHOLD,
            )
            return
        await loop.run_in_executor(None, self.docker.start_container, agent.container_id)
        logger.warning(
            "Agent %s: Aufraeumlauf senkte Belegung auf %.1f%% (< %.0f%%) — Container automatisch neu gestartet",
            agent.id, percent, _STOP_THRESHOLD,
        )
        await self._notify_recovered(agent, used_mb, limit_mb, percent)

    async def _notify_recovered(self, agent: Agent, used_mb: float, limit_mb: float, percent: float) -> None:
        from app.db.session import resilient_session
        from app.models.notification import Notification

        titel = f"{agent.name}: nach Speicherquote-Stopp automatisch aufgeraeumt und neu gestartet"
        nachricht = (
            f"Aufraeumlauf im Volume hat die Belegung auf {percent:.1f}% "
            f"({used_mb:.0f}/{limit_mb:.0f} MB) gesenkt — der Agent laeuft wieder."
        )
        async with resilient_session(session_factory=self._sf) as db:
            db.add(Notification(
                agent_id=agent.id,
                type="success",
                title=titel,
                message=nachricht[:240],
                priority="normal",
                action_url=f"/agents/{agent.id}",
                meta={"type": "disk_quota_auto_recovered", "agent_id": agent.id, "disk_percent": percent},
            ))
            await db.commit()

        if self._redis and getattr(self._redis, "client", None):
            try:
                from app.services.duty_service import _publish_telegram
                await _publish_telegram(self._redis, titel, nachricht)
            except Exception:
                logger.debug("Disk-Quota-Erholungs-Alarm nicht zugestellt", exc_info=True)

    async def _fail_running_tasks_and_alert(self, agent: Agent, stats: dict) -> None:
        """Verbucht laufende Aufgaben als fehlgeschlagen und alarmiert den Betreiber.

        Ohne dies faellt ein Disk-Quota-Stopp nur ins Fehlerlog. Bis
        ``_cleanup_and_maybe_restart`` (Punkt 1) den Behaelter automatisch
        wieder hochfaehrt, bleibt der Agent gestoppt — und ``completed``
        Aufgaben saehen in der Zwischenzeit von aussen wie erledigte Arbeit
        aus. Tagelang unbemerkt geblieben (Issue #714).
        """
        from app.db.session import resilient_session
        from app.models.notification import Notification
        from app.models.task import Task, TaskStatus

        grund = (
            f"Container wegen Speicherquote gestoppt "
            f"({stats['disk_usage_mb']:.0f}/{stats['disk_limit_mb']:.0f} MB, "
            f">= {_STOP_THRESHOLD:.0f}%)."
        )
        anzahl = 0
        async with resilient_session(session_factory=self._sf) as db:
            result = await db.execute(
                select(Task).where(Task.agent_id == agent.id, Task.status == TaskStatus.RUNNING)
            )
            for task in result.scalars().all():
                task.status = TaskStatus.FAILED
                task.error = grund
                task.completed_at = datetime.now(timezone.utc)
                task.notified = True
                task.evict_after = datetime.now(timezone.utc) + _TASK_EVICT_GRACE
                anzahl += 1

            titel = f"{agent.name}: Speicherquote erreicht, Agent angehalten"
            nachricht = grund
            if anzahl:
                nachricht += f" {anzahl} laufende Aufgabe(n) als fehlgeschlagen verbucht."
            nachricht += (
                " Der Agent kann sich nicht selbst befreien (Aufraeumen braucht "
                "einen laufenden Container) — Speicherplatz manuell freigeben, "
                "dann den Agenten neu starten."
            )
            notif = Notification(
                agent_id=agent.id,
                type="error",
                title=titel,
                message=nachricht[:240],
                priority="high",
                action_url=f"/agents/{agent.id}",
                meta={"type": "disk_quota_stop", "agent_id": agent.id, "tasks_failed": anzahl},
            )
            db.add(notif)
            await db.commit()

        if anzahl:
            logger.warning(
                "Agent %s: %d laufende Aufgabe(n) wegen Disk-Quota-Stopp als fehlgeschlagen verbucht",
                agent.id, anzahl,
            )

        # DB-Notification allein erreicht den Betreiber nicht zuverlaessig —
        # priority="high" wird nur ueber POST /notifications/ zu einem
        # Telegram-Push, ein direktes db.add() geht daran vorbei (#610). Der
        # dortige Helfer publiziert unabhaengig vom Web-UI.
        if self._redis and getattr(self._redis, "client", None):
            try:
                from app.services.duty_service import _publish_telegram
                await _publish_telegram(self._redis, titel, nachricht)
            except Exception:
                logger.debug("Disk-Quota-Telegram-Alarm nicht zugestellt", exc_info=True)
