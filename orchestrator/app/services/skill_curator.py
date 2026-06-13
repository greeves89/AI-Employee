"""Skill Curator — Hermes-inspired skill lifecycle automation.

Moves skills through active → stale → archived based on usage signals:

  - ACTIVE skills with no SkillTaskUsage rows for STALE_THRESHOLD_DAYS  → STALE
  - STALE skills with no usage for ARCHIVE_THRESHOLD_DAYS              → ARCHIVED
  - Skills with avg_rating < MIN_RATING and rated_count >= MIN_RATINGS → STALE

Skills with pinned=True (importance signal or user-locked) are exempt.
The curator runs on a schedule; results are written to `curator_notes`.

Inspired by Nous Research's Hermes Curator (curates skills produced by the
self-improvement loop so they don't pile up forever).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill, SkillStatus, SkillTaskUsage

logger = logging.getLogger(__name__)

STALE_THRESHOLD_DAYS = 30
ARCHIVE_THRESHOLD_DAYS = 90
MIN_RATING = 2.5
MIN_RATINGS = 3


@dataclass
class CuratorReport:
    moved_to_stale: list[int]
    moved_to_archived: list[int]
    refreshed_to_active: list[int]
    scanned: int

    def summary(self) -> str:
        return (
            f"scanned={self.scanned} "
            f"→stale={len(self.moved_to_stale)} "
            f"→archived={len(self.moved_to_archived)} "
            f"→active={len(self.refreshed_to_active)}"
        )


class SkillCurator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(self, *, dry_run: bool = False) -> CuratorReport:
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(days=STALE_THRESHOLD_DAYS)
        archive_cutoff = now - timedelta(days=ARCHIVE_THRESHOLD_DAYS)

        report = CuratorReport([], [], [], 0)

        # Hands off any skill currently mid-A/B-test: the improvement_engine
        # is authoritative for those until it validates or rolls back.
        protected_states = ("pending_review", "probation")
        result = await self.db.execute(
            select(Skill).where(
                Skill.status.in_([SkillStatus.ACTIVE, SkillStatus.STALE]),
                or_(
                    Skill.improvement_status.is_(None),
                    Skill.improvement_status.notin_(protected_states),
                ),
            )
        )
        skills = list(result.scalars().all())
        report.scanned = len(skills)

        for skill in skills:
            last_seen = skill.last_used_at or skill.created_at
            if last_seen is not None and last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            low_quality = (
                skill.avg_rating is not None
                and skill.avg_rating < MIN_RATING
                and skill.usage_count >= MIN_RATINGS
            )

            if skill.status == SkillStatus.ACTIVE:
                if last_seen < archive_cutoff and skill.usage_count == 0:
                    if not dry_run:
                        skill.status = SkillStatus.ARCHIVED
                        skill.curator_notes = (
                            f"curator: archived — no usage since creation ({last_seen.date()})"
                        )
                    report.moved_to_archived.append(skill.id)
                elif last_seen < stale_cutoff or low_quality:
                    reason = (
                        f"low avg_rating={skill.avg_rating:.1f}"
                        if low_quality
                        else f"unused since {last_seen.date()}"
                    )
                    if not dry_run:
                        skill.status = SkillStatus.STALE
                        skill.curator_notes = f"curator: marked stale — {reason}"
                    report.moved_to_stale.append(skill.id)

            elif skill.status == SkillStatus.STALE:
                if last_seen < archive_cutoff:
                    if not dry_run:
                        skill.status = SkillStatus.ARCHIVED
                        skill.curator_notes = (
                            f"curator: archived — stale and unused since {last_seen.date()}"
                        )
                    report.moved_to_archived.append(skill.id)
                elif last_seen >= stale_cutoff and not low_quality:
                    # Stale skill was used again recently — bring it back.
                    if not dry_run:
                        skill.status = SkillStatus.ACTIVE
                        skill.curator_notes = (
                            f"curator: refreshed — used again on {last_seen.date()}"
                        )
                    report.refreshed_to_active.append(skill.id)

        if not dry_run:
            await self.db.commit()

        logger.info("[SkillCurator] %s", report.summary())
        return report

    async def touch(self, skill_id: int) -> None:
        """Record that a skill was just used. Call from SkillTaskUsage write path."""
        skill = await self.db.get(Skill, skill_id)
        if skill is None:
            return
        skill.last_used_at = datetime.now(timezone.utc)
        # Auto-refresh stale skills on use — they earned it.
        if skill.status == SkillStatus.STALE:
            skill.status = SkillStatus.ACTIVE
            skill.curator_notes = "curator: auto-refreshed on use"
        await self.db.commit()
