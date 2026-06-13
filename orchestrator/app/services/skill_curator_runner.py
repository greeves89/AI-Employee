"""Background runner for the Hermes-inspired SkillCurator.

Runs the curator once at startup (delayed by GRACE_SECONDS so the rest
of the app is up) and then every CURATOR_INTERVAL_SECONDS — by default
once a day.

Lives in its own module so main.py stays import-cheap.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app.services.skill_curator import SkillCurator

logger = logging.getLogger(__name__)

GRACE_SECONDS = 60
CURATOR_INTERVAL_SECONDS = 86400  # daily


class SkillCuratorRunner:
    def __init__(self, session_factory: Callable):
        self._session_factory = session_factory
        self._stop = asyncio.Event()

    async def run(self) -> None:
        try:
            await asyncio.sleep(GRACE_SECONDS)
        except asyncio.CancelledError:
            return

        while not self._stop.is_set():
            try:
                async with self._session_factory() as session:
                    report = await SkillCurator(session).run()
                    logger.info("[SkillCuratorRunner] %s", report.summary())
            except Exception as e:
                logger.warning("[SkillCuratorRunner] tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CURATOR_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()
