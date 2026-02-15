import asyncio
import logging
import uuid
from datetime import datetime, timezone

from docker.errors import APIError, NotFound
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AGENT_VERSION, settings
from app.models.agent import Agent, AgentState
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

DEFAULT_CLAUDE_MD = """# Agent System Instructions

## Environment
- Workspace: `/workspace/` (persistent across tasks)
- Shared files: `/shared/` (all agents can read/write)
- Team directory: `/shared/team.json`
- Knowledge base: `/workspace/knowledge.md` (my role, skills, learnings)

## MCP Tools (IMPORTANT!)
I have powerful MCP (Model Context Protocol) tools available. These appear as native
tools in my tool list - I use them like any other tool (no bash commands needed).

### Memory Tools (mcp-memory)
I have persistent long-term memory that survives across ALL conversations and tasks.
**I MUST use this to remember important things!**

- **memory_save** - Save important information to memory
  - Categories: preference, contact, project, procedure, decision, fact, learning
  - When to save: user preferences, corrections/learnings, contacts, project context,
    recurring procedures, important decisions, facts (company info, URLs, etc.)
  - Use importance 1-5 (1=trivial, 3=normal, 5=critical)
- **memory_search** - Search memories by keyword and/or category
  - **At the START of every conversation**: Search for recent memories!
  - Before starting any task: search for relevant context
- **memory_list** - List all memories, optionally filtered by category
- **memory_delete** - Delete a specific memory by ID

### Notification Tools (mcp-notifications)
- **notify_user** - Send notification to the user (Web UI + Telegram for high/urgent)
  - Types: info (blue), warning (amber), error (red), success (green)
  - Priorities: low, normal, high (Telegram), urgent (Telegram + flashing)
  - **ALWAYS** notify when a long task completes
  - Use high/urgent sparingly (it sends Telegram push notifications)
- **request_approval** - Ask user to approve a critical action before proceeding
  - Presents clickable options in the UI (e.g. ["Send now", "Edit first", "Cancel"])
  - **ALWAYS** ask approval before: sending emails, deleting files, purchases, external API calls

### Orchestrator Tools (mcp-orchestrator)
- **create_task** - Create a new task (for self or another agent)
- **list_tasks** - List tasks assigned to me (filter by status)
- **list_team** - See all team members with roles and status
- **send_message** - Send a text message to another agent
- **create_schedule** - Create a recurring task schedule
- **list_schedules** - List all recurring schedules
- **manage_schedule** - Pause, resume, or delete a schedule

### Legacy CLI (still available as fallback)
The `ai-team` bash command still works for all the above operations.
Run `ai-team help` for usage. Prefer MCP tools over CLI when possible.

## Knowledge Access
My knowledge, role, and learned patterns are stored in `/workspace/knowledge.md`.
- Read it at the start of conversations to recall my role and context
- Update it after completing tasks with new learnings
- Use `grep` to search for specific knowledge when needed

## Workspace Organization (IMPORTANT!)
I MUST keep my workspace organized with proper directories:
- `/workspace/transfer/` - **All output files for the user go HERE** (PDFs, reports, exports, downloads)
- `/workspace/scripts/` - Python scripts, automation code, tools
- `/workspace/data/` - Raw data, downloaded content, caches
- `/workspace/docs/` - Documentation, notes, research

**Rules:**
- NEVER dump files directly in /workspace root - always use subdirectories
- When creating files the user requested (PDFs, reports, exports): put in `/workspace/transfer/`
- When creating scripts: put in `/workspace/scripts/`
- Create additional subdirectories as needed
- Use `mkdir -p` to create directories before writing files
"""

DEFAULT_KNOWLEDGE_MD = """# Agent Knowledge Base

## Onboarding Status: NOT COMPLETED
**IMPORTANT: On my FIRST conversation, I MUST conduct an onboarding interview!**

### Onboarding Interview Steps:
1. Introduce myself and explain that I'm a new team member that needs setup
2. Ask the user: "What role should I fill?" (e.g., Developer, Researcher, Writer, Analyst, DevOps, etc.)
3. Ask: "What are my main responsibilities?"
4. Ask: "Are there things I should NOT do or be careful about?"
5. Ask: "What tools, languages, or frameworks should I focus on?"
6. Ask: "Any other preferences or instructions?"
7. After getting answers, update THIS file (knowledge.md) with my complete profile
8. Change "Onboarding Status" above to "COMPLETED"
9. Write a brief summary of my role to /shared/team.json (read existing, add myself)

## My Role
<!-- Filled in after onboarding interview -->

## My Responsibilities
<!-- Filled in after onboarding interview -->

## Boundaries & Rules
<!-- Filled in after onboarding interview -->

## Tech Stack & Skills
<!-- Filled in after onboarding interview -->

## Learned Patterns
<!-- I update this section after each task with new learnings -->

## Errors & Fixes
<!-- Common errors and how I resolved them -->
"""


class AgentManager:
    """Manages the lifecycle of agent Docker containers."""

    def __init__(self, db: AsyncSession, docker: DockerService, redis: RedisService):
        self.db = db
        self.docker = docker
        self.redis = redis

    async def create_agent(self, name: str, model: str | None = None, role: str | None = None, integrations: list[str] | None = None) -> Agent:
        agent_id = uuid.uuid4().hex[:8]
        container_name = f"ai-agent-{name.lower().replace(' ', '-')}-{agent_id}"
        volume_name = f"workspace-{agent_id}"
        session_volume = f"claude-session-{agent_id}"
        model = model or settings.default_model

        # Build auth environment (API key or OAuth token)
        auth_env = {}
        if settings.anthropic_api_key:
            auth_env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        elif settings.claude_code_oauth_token:
            auth_env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token

        # Create Docker container with workspace + session + shared volumes
        container = self.docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment={
                "AGENT_ID": agent_id,
                "AGENT_NAME": name,
                "AGENT_ROLE": role or "",
                "REDIS_URL": settings.redis_url_internal,
                "ORCHESTRATOR_URL": "http://orchestrator:8000",
                **auth_env,
                "DEFAULT_MODEL": model,
                "MAX_TURNS": str(settings.max_turns),
            },
            volume_name=volume_name,
            session_volume_name=session_volume,
            shared_volume_name="ai-employee-shared",
            network=settings.agent_network,
            memory_limit=settings.agent_memory_limit,
            cpu_quota=settings.agent_cpu_quota,
        )

        # Initialize CLAUDE.md (system instructions) and knowledge.md (agent knowledge)
        try:
            self.docker.write_file_in_container(
                container.id, "/workspace/CLAUDE.md", DEFAULT_CLAUDE_MD
            )
            self.docker.write_file_in_container(
                container.id, "/workspace/knowledge.md", DEFAULT_KNOWLEDGE_MD
            )
            logger.info(f"Initialized CLAUDE.md + knowledge.md for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Could not initialize agent files: {e}")

        # Update shared team registry
        try:
            self._update_team_registry(container.id, agent_id, name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # Save to DB
        agent = Agent(
            id=agent_id,
            name=name,
            container_id=container.id,
            volume_name=volume_name,
            state=AgentState.RUNNING,
            model=model,
            config={
                "session_volume": session_volume,
                "role": role or "",
                "onboarding_complete": False,
                "integrations": integrations or [],
                "agent_version": AGENT_VERSION,
                "metrics": {"total": 0, "success": 0, "fail": 0, "success_rate": 0.0},
            },
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)

        return agent

    def _update_team_registry(self, container_id: str, agent_id: str, name: str, role: str) -> None:
        """Update the shared team.json with this agent's info."""
        import json as json_module

        # Read existing team.json (or start fresh)
        try:
            _, content = self.docker.exec_in_container(container_id, "cat /shared/team.json")
            team = json_module.loads(content) if content.strip() else {"agents": []}
        except Exception:
            team = {"agents": []}

        # Remove old entry for this agent if exists
        team["agents"] = [a for a in team["agents"] if a.get("id") != agent_id]

        # Add new entry
        team["agents"].append({
            "id": agent_id,
            "name": name,
            "role": role,
            "status": "online",
        })

        self.docker.write_file_in_container(
            container_id, "/shared/team.json", json_module.dumps(team, indent=2)
        )

    async def stop_agent(self, agent_id: str) -> Agent:
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.stop_container(agent.container_id)
            except (NotFound, APIError) as e:
                logger.warning(f"Container {agent.container_id} not found when stopping agent {agent_id}: {e}")
        agent.state = AgentState.STOPPED
        await self.db.commit()
        return agent

    async def start_agent(self, agent_id: str) -> Agent:
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.start_container(agent.container_id)
            except NotFound:
                logger.warning(f"Container {agent.container_id} not found for agent {agent_id}")
                agent.state = AgentState.ERROR
                await self.db.commit()
                raise ValueError(f"Agent container no longer exists. Delete and recreate the agent.")
        agent.state = AgentState.RUNNING
        await self.db.commit()
        return agent

    async def update_agent(self, agent_id: str) -> Agent:
        """Recreate agent container with latest image, preserving all data (volumes)."""
        agent = await self._get_agent(agent_id)
        config = agent.config or {}

        # 1. Stop and remove old container (volumes stay!)
        if agent.container_id:
            try:
                self.docker.stop_container(agent.container_id)
            except (NotFound, APIError):
                pass
            try:
                self.docker.remove_container(agent.container_id)
            except (NotFound, APIError):
                pass

        # 2. Build environment (same as create)
        auth_env = {}
        if settings.anthropic_api_key:
            auth_env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        elif settings.claude_code_oauth_token:
            auth_env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token

        volume_name = agent.volume_name
        session_volume = config.get("session_volume", f"claude-session-{agent_id}")
        role = config.get("role", "")
        model = agent.model or settings.default_model

        container_name = f"ai-agent-{agent.name.lower().replace(' ', '-')}-{agent_id}"

        # 3. Create new container with same volumes
        container = self.docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment={
                "AGENT_ID": agent_id,
                "AGENT_NAME": agent.name,
                "AGENT_ROLE": role,
                "REDIS_URL": settings.redis_url_internal,
                "ORCHESTRATOR_URL": "http://orchestrator:8000",
                **auth_env,
                "DEFAULT_MODEL": model,
                "MAX_TURNS": str(settings.max_turns),
            },
            volume_name=volume_name,
            session_volume_name=session_volume,
            shared_volume_name="ai-employee-shared",
            network=settings.agent_network,
            memory_limit=settings.agent_memory_limit,
            cpu_quota=settings.agent_cpu_quota,
        )

        # 4. Update CLAUDE.md (system instructions) but keep knowledge.md (agent data)
        try:
            self.docker.write_file_in_container(
                container.id, "/workspace/CLAUDE.md", DEFAULT_CLAUDE_MD
            )
            logger.info(f"Updated CLAUDE.md for agent {agent_id} (knowledge.md preserved)")
        except Exception as e:
            logger.warning(f"Could not update CLAUDE.md: {e}")

        # 5. Update team registry
        try:
            self._update_team_registry(container.id, agent_id, agent.name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # 6. Update DB
        agent.container_id = container.id
        agent.state = AgentState.RUNNING
        config["agent_version"] = AGENT_VERSION
        agent.config = config
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(agent, "config")
        await self.db.commit()
        await self.db.refresh(agent)

        logger.info(f"Agent {agent_id} updated to new image version")
        return agent

    async def remove_agent(self, agent_id: str, remove_data: bool = False) -> None:
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.remove_container(agent.container_id)
            except (NotFound, APIError) as e:
                logger.warning(f"Container {agent.container_id} already gone for agent {agent_id}: {e}")
        if remove_data and agent.volume_name:
            self.docker.remove_volume(agent.volume_name)
            # Also remove session volume
            config = agent.config or {}
            session_vol = config.get("session_volume")
            if session_vol:
                self.docker.remove_volume(session_vol)
        await self.db.delete(agent)
        await self.db.commit()

    async def get_agent_with_metrics(self, agent_id: str, include_stats: bool = True) -> dict:
        agent = await self._get_agent(agent_id)

        # Sync DB state with actual Docker container status (lightweight check)
        if agent.container_id and agent.state not in (AgentState.STOPPED, AgentState.ERROR):
            container_status = self.docker.get_container_status(agent.container_id)
            if container_status == "unknown":
                agent.state = AgentState.ERROR
                await self.db.commit()
                logger.warning(f"Agent {agent_id} container is gone, marked as ERROR")
            elif container_status == "exited":
                agent.state = AgentState.STOPPED
                await self.db.commit()
                logger.info(f"Agent {agent_id} container exited, marked as STOPPED")

        config = agent.config or {}

        # Check if agent version is outdated
        update_available = False
        stored_version = config.get("agent_version")
        if stored_version != AGENT_VERSION:
            update_available = True

        result = {
            "id": agent.id,
            "name": agent.name,
            "container_id": agent.container_id,
            "state": agent.state,
            "model": agent.model,
            "role": config.get("role", ""),
            "onboarding_complete": config.get("onboarding_complete", False),
            "integrations": config.get("integrations", []),
            "update_available": update_available,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }

        # Add live metrics from Redis
        status = await self.redis.get_agent_status(agent_id)
        result["current_task"] = status.get("current_task", "")
        result["queue_depth"] = await self.redis.get_queue_depth(agent_id)

        # Add Docker stats if running (run in thread pool to avoid blocking)
        if include_stats and agent.container_id and agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            try:
                loop = asyncio.get_event_loop()
                stats = await loop.run_in_executor(
                    None, self.docker.get_container_stats, agent.container_id
                )
                result["cpu_percent"] = stats["cpu_percent"]
                result["memory_usage_mb"] = stats["memory_usage_mb"]
            except Exception:
                result["cpu_percent"] = None
                result["memory_usage_mb"] = None

        return result

    async def list_agents(self) -> list[Agent]:
        result = await self.db.execute(select(Agent).order_by(Agent.created_at.desc()))
        return list(result.scalars().all())

    async def _get_agent(self, agent_id: str) -> Agent:
        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise ValueError(f"Agent {agent_id} not found")
        return agent
