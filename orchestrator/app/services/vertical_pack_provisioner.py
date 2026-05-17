"""Vertical pack provisioner — applies an industry starter kit in one step.

Given a pack definition (see app.core.vertical_packs), this creates one agent
per template in the pack, assigns the templates' skills, writes the pack's seed
knowledge entries, and optionally queues a first demo task.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_manager import AgentManager
from app.models.agent_template import AgentTemplate
from app.models.knowledge import KnowledgeEntry
from app.models.skill import AgentSkillAssignment, Skill
from app.models.task import Task, TaskStatus
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


async def provision_pack(
    pack: dict,
    user_id: str | None,
    db: AsyncSession,
    docker: DockerService,
    redis: RedisService,
) -> dict:
    """Provision a vertical pack for a user. Returns a summary of what was created."""
    created_agents: list[dict] = []
    knowledge_created = 0
    demo_task_id: str | None = None

    manager = AgentManager(db, docker, redis)

    # 1. Create one agent per template in the pack.
    for template_name in pack.get("template_names", []):
        template = await db.scalar(
            select(AgentTemplate).where(AgentTemplate.name == template_name)
        )
        if not template:
            logger.warning(f"[VerticalPack] Template '{template_name}' not found — skipping")
            continue

        agent = await manager.create_agent(
            name=f"{template.display_name} ({pack['name']})",
            model=template.model,
            role=template.role,
            integrations=template.integrations or [],
            permissions=template.permissions or [],
            user_id=user_id,
        )

        # Knowledge / CLAUDE.md from the template
        if template.claude_md and agent.container_id:
            try:
                docker.write_file_in_container(
                    agent.container_id, "/workspace/CLAUDE.md", template.claude_md
                )
            except Exception as e:
                logger.warning(f"[VerticalPack] CLAUDE.md write failed: {e}")
        if template.knowledge_template and agent.container_id:
            try:
                docker.write_file_in_container(
                    agent.container_id, "/workspace/knowledge.md", template.knowledge_template
                )
            except Exception as e:
                logger.warning(f"[VerticalPack] knowledge.md write failed: {e}")

        # Assign the template's skills
        for skill_id in (template.skill_ids or []):
            skill = await db.get(Skill, skill_id)
            if skill and skill.status == "active":
                db.add(AgentSkillAssignment(
                    agent_id=agent.id, skill_id=skill_id, assigned_by="vertical_pack",
                ))

        created_agents.append({"id": agent.id, "name": agent.name})

    await db.commit()

    # 2. Seed knowledge entries (skip titles that already exist — title is unique).
    for entry in pack.get("knowledge_entries", []):
        title = entry["title"]
        exists = await db.scalar(select(KnowledgeEntry).where(KnowledgeEntry.title == title))
        if exists:
            continue
        db.add(KnowledgeEntry(
            title=title,
            content=entry.get("content", ""),
            tags=entry.get("tags", []),
            created_by="vertical_pack",
            user_id=user_id,
        ))
        knowledge_created += 1
    await db.commit()

    # 3. Optionally queue a first demo task on the first agent.
    demo = pack.get("demo_task")
    if demo and created_agents:
        agent_id = created_agents[0]["id"]
        demo_task_id = uuid.uuid4().hex[:12]
        db.add(Task(
            id=demo_task_id,
            title=demo["title"],
            prompt=demo["prompt"],
            status=TaskStatus.QUEUED,
            agent_id=agent_id,
            metadata_={"source": "vertical_pack", "pack": pack["slug"]},
        ))
        await db.commit()
        try:
            import json
            await redis.push_task(agent_id, json.dumps({
                "id": demo_task_id,
                "prompt": demo["prompt"],
                "title": demo["title"],
                "model": None,
                "priority": "low",
            }))
        except Exception as e:
            logger.warning(f"[VerticalPack] Could not queue demo task: {e}")

    return {
        "pack": pack["slug"],
        "agents": created_agents,
        "knowledge_created": knowledge_created,
        "demo_task_id": demo_task_id,
    }
