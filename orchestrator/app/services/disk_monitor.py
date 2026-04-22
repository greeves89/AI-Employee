"""Disk quota monitor for agent workspaces.

Runs every 5 minutes, checks /workspace usage per agent against the configured
soft quota (agent_workspace_size_gb). Writes a warning file the agent can read,
and stops the agent if usage exceeds 95 % of the quota.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent, AgentState

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 300  # 5 minutes
_WARN_THRESHOLD = 80.0
_STOP_THRESHOLD = 95.0


class DiskMonitorService:
    def __init__(self, session_factory, docker_service) -> None:
        self._sf = session_factory
        self.docker = docker_service
        self._running = True

    async def run(self) -> None:
        await asyncio.sleep(60)  # brief startup delay
        while self._running:
            try:
                await self._check_all_agents()
            except Exception as exc:
                logger.error("Disk monitor cycle failed: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    async def _check_all_agents(self) -> None:
        async with self._sf() as db:
            result = await db.execute(
                select(Agent).where(
                    Agent.state.in_([AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING])
                )
            )
            agents = list(result.scalars().all())

        loop = asyncio.get_event_loop()
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
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.docker.stop_container, agent.container_id)
        except Exception as exc:
            logger.error("Failed to stop disk-full agent %s: %s", agent.id, exc)
