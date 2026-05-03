"""Skill Auto-Injection — automatically assign skills to agents based on paths and roles.

When a task is created, this service:
1. Extracts file-like paths from the task prompt
2. Matches them against skill `paths` glob patterns (fnmatch)
3. Matches the agent's role against skill `roles` lists
4. Creates AgentSkillAssignment records with assigned_by="auto:path" or "auto:role"
"""

import fnmatch
import logging
import re
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.skill import AgentSkillAssignment, Skill, SkillStatus

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(
    r"""(?:^|[\s"'`(,])"""           # preceded by whitespace, quote, backtick, paren, or start
    r"""("""
    r"""(?:[a-zA-Z]:)?"""            # optional Windows drive letter
    r"""(?:[/\\][\w.*\-]+)+"""       # one or more path segments starting with / or \
    r"""(?:\.\w+)?"""                # optional file extension
    r""")""",
    re.MULTILINE,
)


def extract_paths_from_text(text: str) -> list[str]:
    """Extract file-system-like paths from free text (task prompt)."""
    matches = _PATH_RE.findall(text)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        normalized = m.replace("\\", "/")
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def match_paths(extracted: list[str], patterns: list[str]) -> bool:
    """Return True if any extracted path matches any glob pattern."""
    for path in extracted:
        for pattern in patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
    return False


async def _get_existing_assignments(db: AsyncSession, agent_id: str) -> set[int]:
    """Return set of skill IDs already assigned to this agent."""
    rows = await db.execute(
        select(AgentSkillAssignment.skill_id)
        .where(AgentSkillAssignment.agent_id == agent_id)
    )
    return {r[0] for r in rows.all()}


async def _skills_with_paths(db: AsyncSession) -> Sequence[Skill]:
    result = await db.execute(
        select(Skill)
        .where(Skill.status == SkillStatus.ACTIVE)
        .where(Skill.paths.isnot(None))
    )
    return result.scalars().all()


async def _skills_with_roles(db: AsyncSession) -> Sequence[Skill]:
    result = await db.execute(
        select(Skill)
        .where(Skill.status == SkillStatus.ACTIVE)
        .where(Skill.roles.isnot(None))
    )
    return result.scalars().all()


async def auto_inject_skills(
    db: AsyncSession,
    agent_id: str,
    task_prompt: str,
) -> list[dict]:
    """Auto-assign skills to an agent based on task prompt paths and agent role.

    Returns a list of dicts describing what was injected, e.g.:
    [{"skill_id": 5, "skill_name": "alembic-migration", "assigned_by": "auto:path"}]
    """
    injected: list[dict] = []
    existing = await _get_existing_assignments(db, agent_id)

    # --- Path-based injection ---
    extracted_paths = extract_paths_from_text(task_prompt)
    if extracted_paths:
        path_skills = await _skills_with_paths(db)
        for skill in path_skills:
            if skill.id in existing:
                continue
            if skill.paths and match_paths(extracted_paths, skill.paths):
                db.add(AgentSkillAssignment(
                    agent_id=agent_id,
                    skill_id=skill.id,
                    assigned_by="auto:path",
                ))
                existing.add(skill.id)
                injected.append({
                    "skill_id": skill.id,
                    "skill_name": skill.name,
                    "assigned_by": "auto:path",
                })

    # --- Role-based injection ---
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    agent_role = ""
    if agent and agent.config:
        agent_role = agent.config.get("role", "")

    if agent_role:
        role_skills = await _skills_with_roles(db)
        for skill in role_skills:
            if skill.id in existing:
                continue
            if skill.roles and agent_role in skill.roles:
                db.add(AgentSkillAssignment(
                    agent_id=agent_id,
                    skill_id=skill.id,
                    assigned_by="auto:role",
                ))
                existing.add(skill.id)
                injected.append({
                    "skill_id": skill.id,
                    "skill_name": skill.name,
                    "assigned_by": "auto:role",
                })

    if injected:
        await db.commit()
        names = ", ".join(f"{i['skill_name']} ({i['assigned_by']})" for i in injected)
        logger.info(f"Auto-injected {len(injected)} skill(s) for agent {agent_id}: {names}")

    return injected
