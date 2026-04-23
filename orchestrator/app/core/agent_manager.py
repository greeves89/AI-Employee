import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from docker.errors import APIError, NotFound
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import AGENT_VERSION, settings
from app.core.encryption import decrypt_token
from app.dependencies import make_agent_token
from app.models.agent import Agent, AgentState
from app.models.mcp_server import McpServer
from app.models.oauth_integration import OAuthIntegration, OAuthProvider
from app.models.schedule import Schedule
from app.services.docker_service import DockerService
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Permission Packages - configurable sudo rules per agent
# ──────────────────────────────────────────────
PERMISSION_PACKAGES = {
    "package-install": {
        "label": "Paketinstallation",
        "description": "System-Pakete installieren und verwalten (apt-get, dpkg)",
        "icon": "package",
        "sudoers_commands": [
            "/usr/bin/apt-get update",
            "/usr/bin/apt-get install *",
            "/usr/bin/apt-get remove *",
            "/usr/bin/dpkg -i *",
        ],
    },
    "system-config": {
        "label": "Systemkonfiguration",
        "description": "Dateiberechtigungen und Verzeichnisse verwalten (chmod, chown, mkdir, ln)",
        "icon": "settings",
        "sudoers_commands": [
            "/usr/bin/chmod *",
            "/usr/bin/chown *",
            "/usr/bin/mkdir *",
            "/usr/bin/ln *",
        ],
    },
    "full-access": {
        "label": "Voller Root-Zugriff",
        "description": "Uneingeschraenkter sudo-Zugriff - fuer Entwicklung und Testing",
        "icon": "shield-off",
        "sudoers_commands": ["ALL"],
    },
}

# Default permissions for new agents
DEFAULT_PERMISSIONS = ["package-install"]


def generate_sudoers(permissions: list[str]) -> str:
    """Generate sudoers file content from permission package names."""
    if not permissions:
        return ""

    # Full access overrides everything
    if "full-access" in permissions:
        return "agent ALL=(ALL) NOPASSWD: ALL\n"

    # Collect all allowed commands
    commands: list[str] = []
    for perm_name in permissions:
        pkg = PERMISSION_PACKAGES.get(perm_name)
        if pkg:
            commands.extend(pkg["sudoers_commands"])

    if not commands:
        return ""

    # Build sudoers line: agent ALL=(ALL) NOPASSWD: cmd1, cmd2, ...
    cmd_list = ", ".join(commands)
    return f"agent ALL=(ALL) NOPASSWD: {cmd_list}\n"


PROACTIVE_PROMPT = """You are running in PROACTIVE mode. Your job is to check for pending work and DO IT.

## FIRST: Load context (do this EVERY proactive run!)
1. Read /workspace/knowledge.md for your role, skills, and learned patterns
2. Use knowledge_search(query: "") to check the shared knowledge base for recent entries
3. Use memory_search(query: "") to recall recent memories and context
4. Use list_todos to see pending work

## SCOPE RULES (CRITICAL — read first!)
- **Only work on repos YOU own** (where `gh repo view` shows your org/user as owner).
- **NEVER work on external/third-party repos** (cloned forks, upstream repos, other people's code).
  Before working on any repo, run `gh repo view --json owner -q .owner.login` — if the owner is
  not your org, SKIP that repo entirely.
- **NEVER attempt operations your token can't do** (forking, PRs on repos you don't own).
  If `gh` returns a permission error, stop immediately and move on.

## STEP 1: CHECK GITHUB ISSUES (always do this first)

Check your own repositories in /workspace for open GitHub issues:
1. Find repos: `find /workspace -maxdepth 3 -name .git -type d`
2. For each repo, verify ownership first (see SCOPE RULES above). Skip repos you don't own.
3. For owned repos, run: `cd <repo> && gh issue list --state open --limit 20`
4. For NEW issues you haven't seen before:
   a. Create a feature branch: `git checkout -b fix/issue-<number>-<short-desc>`
   b. Implement the fix, run build/tests to verify
   c. Commit with a message referencing the issue: `fix: <description> (fixes #<number>)`
   d. Push the branch: `git push -u origin <branch-name>`
   e. Create a Pull Request: `gh pr create --title "Fix #<number>: <desc>" --body "Fixes #<number>"`
   f. The PR body with "Fixes #N" will auto-close the issue when merged
   g. Use `notify_user` to inform the user: "Created PR #X for issue #N in <repo>"
   - If the issue needs user input: create a TODO with `update_todos` referencing the issue
5. Save a memory with `memory_save` (key: "last_github_check") noting which issues you reviewed

## STEP 2: WORK ON TODOs

1. Use `list_todos` to see all pending and in_progress items
2. **If there are ANY pending TODOs: Pick the highest-priority one and DO THE WORK.**
3. Mark it in_progress with `update_todos`, then implement it fully (write code, run tests, etc.)
4. After completing: mark it as completed with `complete_todo`
5. After completing one TODO, go back to step 1 and pick the next one.

**CRITICAL: TODOs are YOUR assigned tasks. They exist because they need to be done by YOU.**
**DO NOT analyze whether TODOs are "genuine proactive work" — just DO them.**
**DO NOT skip TODOs because they seem like "the user's plan" — YOU are the one who must execute that plan.**
**DO NOT just list/summarize TODOs and then stop — that is a FAILURE. You must pick one and complete it.**

If a TODO is too vague, break it down with `update_todos` into concrete subtasks, then work on the first one.

## STEP 3: GIT HYGIENE (after completing work)
- Always push your work: `git push`
- If you fixed an issue: create a PR with `gh pr create` (not just a branch!)
- If you completed a TODO that involved code: commit, push, and optionally create a PR
- NEVER leave uncommitted or unpushed work

## STEP 4: REVIEW & UPDATE KNOWLEDGE (do this EVERY proactive run, after completing work)

### Update knowledge.md
- Read `/workspace/knowledge.md` — this is your persistent profile and skill record
- Add any new patterns you learned to "## Learned Patterns"
- Add errors you encountered and how you fixed them to "## Errors & Fixes"
- If your responsibilities expanded, update those sections too
- Keep it concise but comprehensive — you read this file at the start of every task

### Review and maintain long-term memory
- Use `memory_list` to review your memories
- Delete outdated or incorrect memories with `memory_delete`
- Update memories that need correction with `memory_save` (same key = overwrite)

### General workspace maintenance
- Check workspace organization, clean up temp files
- Any follow-up items from previous work?

## ERROR HANDLING (CRITICAL — read before broadcasting!)
- If CLI tools fail, search `knowledge_search` and `memory_search` for known fixes FIRST
- **NEVER send error messages to Telegram** like "CLI not available" or "connection failed"
  — these spam the user and provide no value. Fix the error silently or log it internally.
- Only notify the user about ACTIONABLE problems that require their input
- If something is genuinely broken and you cannot fix it after researching: use `notify_user`
  with priority "high" ONCE, not a broadcast. Include what you tried and what you need.

## WHEN DONE:
- Notify the user via `notify_user` about what you accomplished (resolved issues, completed TODOs, PRs created)
- **Send a Telegram broadcast ONLY if you accomplished real work** (resolved issues, completed TODOs, created PRs).
  Do NOT broadcast "nothing to do" or error messages. Keep it short (2-5 sentences):
  curl -s -X POST $ORCHESTRATOR_URL/api/v1/telegram/broadcast \
    -H "X-Agent-ID: $AGENT_ID" -H "Authorization: Bearer $AGENT_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"text": "YOUR SUMMARY HERE"}'
- If truly nothing to do (ZERO TODOs, no open issues, workspace clean): respond "No proactive actions needed." (NO broadcast!)
- Do NOT invent new tasks or create busywork. But ALWAYS complete existing TODOs and check issues.

IMPORTANT: If you haven't completed onboarding yet, skip the proactive check.
"""

DEFAULT_CLAUDE_MD = """# Agent System Instructions

## Communication (CRITICAL!)
- **ALWAYS respond to the user** with a clear, helpful text message. Never end silently.
- After completing an action (tool use, code change, file creation), summarize what you did.
- Use the user's language — if they write in German, respond in German.
- Keep responses concise but informative. The user should never wonder "did it work?"
- For multi-step tasks, provide brief progress updates via `send_telegram` if available.

## Environment
- Workspace: `/workspace/` (persistent across tasks)
- Shared files: `/shared/` (all agents can read/write)
- Team directory: `/shared/team.json`
- Knowledge base: `/workspace/knowledge.md` (my role, skills, learnings)

## MCP Tools (IMPORTANT!)
I have powerful MCP (Model Context Protocol) tools available. These appear as native
tools in my tool list - I use them like any other tool (no bash commands needed).

**CRITICAL: I MUST use MCP tools for memory, NOT the Write tool or MEMORY.md!**
The built-in auto-memory (MEMORY.md) is NOT visible in the Web UI.
Only `memory_save` stores data that the user can see in the Memory tab.

**CRITICAL: I MUST NEVER use the built-in TodoWrite tool!**
The built-in TodoWrite is LOCAL ONLY - the user CANNOT see it in the Web UI Todo tab!
ALWAYS use the MCP tools `update_todos` / `complete_todo` / `list_todos` instead.
These save to the database and are visible in the Todo tab in real-time.

### Memory Tools (mcp-memory)
I have persistent long-term memory that survives across ALL conversations and tasks.
**I MUST use `memory_save` to remember important things!**
**I MUST NEVER use Write/Edit to write to MEMORY.md for memory storage!**

- **memory_save** - Save important information to memory (ALWAYS USE THIS!)
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
- **send_telegram** - Send a DIRECT message to the user via Telegram chat
  - Use this for **live progress updates** during work (e.g. "Step 1/3 done", "Building...", "Found issue, fixing")
  - **ALWAYS use this frequently** to keep the user informed about what you are doing!
  - Send updates at every major step, not just at the end
  - The user expects regular status messages via Telegram
- **notify_user** - Send notification to the Web UI notification center (+ Telegram for high/urgent)
  - Types: info (blue), warning (amber), error (red), success (green)
  - Priorities: low, normal, high (Telegram), urgent (Telegram + flashing)
  - Use for completed tasks, errors, important events
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

### TODO Tools (mcp-orchestrator) - VISIBLE IN WEB UI!
**⚠️ NEVER use the built-in TodoWrite tool - it is NOT visible to the user!**
**⚠️ ONLY use these MCP tools for TODOs - they save to the database!**
TODOs are persistent and displayed in the "Todos" tab for the user to see.
- **list_todos** - List my TODO items (filter by status or task_id). **ALWAYS call this FIRST!**
- **update_todos** - Add/replace pending TODOs (completed TODOs are preserved automatically)
  - ⚠️ **ALWAYS `list_todos` first** before using this! Existing TODOs are the user's work plan!
  - Only replaces pending/in_progress items, completed ones are never deleted
  - Include task_id to link TODOs to a specific task
- **complete_todo** - Mark a single TODO as completed by ID
**When starting a task: `list_todos` first → work on existing ones → only add NEW if needed!**

### Knowledge Base Tools (mcp-knowledge) — SHARED ACROSS ALL AGENTS!
All agents share a central knowledge base. **USE THIS ACTIVELY!**
- **knowledge_search** - Search the shared knowledge base by keyword and/or tag
  - **ALWAYS search BEFORE asking the user** for information you might already know!
  - Search when you encounter a problem, need context, or start a new topic
  - Example: `knowledge_search(query: "telegram")` to find Telegram-related knowledge
- **knowledge_read** - Read a specific knowledge entry by exact title
  - Use when you know the title (e.g. from a [[backlink]] in another entry)
- **knowledge_write** - Write/update a knowledge entry (all agents can read it)
  - Use [[Title]] syntax to link between entries, #tags for categorization
  - Write company knowledge, processes, decisions, contacts, project docs here

### Legacy CLI (still available as fallback)
The `ai-team` bash command still works for all the above operations.
Run `ai-team help` for usage. Prefer MCP tools over CLI when possible.

## Knowledge Access (CRITICAL — use EVERY session!)
I have TWO knowledge sources and MUST use BOTH:

### 1. Personal knowledge file: `/workspace/knowledge.md`
- **Read it at the START of every task** to recall my role, skills, and past learnings
- **Update it at the END of every task** with new patterns, errors & fixes, and insights
- Sections to maintain: "Learned Patterns", "Errors & Fixes", role/responsibilities if they change
- This is my persistent profile — it makes me better over time

### 2. Shared Knowledge Base (MCP tools)
- **ALWAYS `knowledge_search` BEFORE asking the user or giving up!**
- When I encounter a problem → search knowledge base first
- When I need to know how something works → search knowledge base first
- When the user asks about a topic → search knowledge base for existing entries
- **After learning something new → `knowledge_write` to share with all agents**

### Self-Research Rule (CRITICAL!)
Before telling the user "I don't know" or "CLI not available" or sending error messages:
1. `knowledge_search` for the topic
2. `memory_search` for related memories
3. Read `/workspace/knowledge.md` for patterns and fixes
4. `grep` or `find` in the workspace for relevant files
5. ONLY THEN ask the user if still stuck

## Proactive Mode
I periodically wake up (via schedule) to check if there is work to do on my own.
The proactive prompt gives detailed instructions each time. Key principles:
- Only work on repos I own (check with `gh repo view --json owner`)
- Always push code and create PRs (never leave work only local)
- Always close issues via PR with "Fixes #N"
- Notify user about completed work via `notify_user`
- Execute genuine work only (no busywork!)

## TODO Management (CRITICAL - NEVER USE TodoWrite!)
The built-in TodoWrite tool is BROKEN for this platform - it does NOT save to the database!

**ALWAYS check existing TODOs first before creating new ones!**
1. **FIRST: `list_todos`** - Check what TODOs already exist
2. **Work on existing TODOs** - Pick highest-priority pending item, mark in_progress, do the work
3. **Complete with `complete_todo`** - Mark individual items as done
4. **Only add NEW TODOs** if there is genuinely new work not already covered
5. **NEVER blindly replace** the entire TODO list

TODOs persist across sessions and container restarts. Previous TODOs are the user's work plan!
**If I accidentally use TodoWrite, the user sees NOTHING. Always use MCP tools!**

## Git Workflow (CRITICAL!)
- **Always push** - Never leave work only local. Run `git push` after committing.
- **Create PRs** for any non-trivial change: `gh pr create --title "..." --body "..."`
- **Reference issues** in commits and PRs: `fixes #N` auto-closes the issue
- **Only work on own repos** - Check ownership with `gh repo view --json owner` first
- **Never work on third-party repos** you don't own (forks, upstream, external)

## General Work Principles
- **Understand before acting** - Read existing files, docs, and context before making changes
- **Be consistent** - Match the style and patterns already used in a project
- **Verify your work** - Run build/tests before committing. NEVER commit broken code
- **Save learnings** - Use `memory_save` (category: "learning") for important discoveries

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

## Disk Quota (IMPORTANT!)
My workspace has a soft quota of **$AGENT_WORKSPACE_SIZE_GB GB**.
- **Before large operations** (downloads, builds, cloning repos): check with `df -h /workspace` or `du -sh /workspace`
- **Check remaining space**: `echo "Used: $(du -sh /workspace 2>/dev/null | cut -f1)"`
- **Warning file**: If `/workspace/.disk_warning` exists, read it — I am running low on space and MUST clean up first
- **Clean up**: `rm -rf /workspace/data/cache /workspace/tmp && find /workspace -name '*.log' -delete`
- **Find large files**: `du -sh /workspace/* | sort -rh | head -10`
- If I ignore disk warnings and run out of space, my container will be stopped automatically
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

    @staticmethod
    def _build_provider_env(agent_provider: str | None = None) -> dict[str, str]:
        """Build environment variables for the active model provider.

        Uses per-agent provider if set, otherwise falls back to global settings.

        - ``anthropic`` (default): ANTHROPIC_API_KEY *or* CLAUDE_CODE_OAUTH_TOKEN
        - ``bedrock``: CLAUDE_CODE_USE_BEDROCK + AWS credentials
        - ``vertex``:  CLAUDE_CODE_USE_VERTEX + GCP credentials
        - ``foundry``: CLAUDE_CODE_USE_FOUNDRY + Azure Foundry credentials
        """
        provider = agent_provider or settings.model_provider

        if provider == "bedrock":
            env: dict[str, str] = {"CLAUDE_CODE_USE_BEDROCK": "1"}
            if settings.aws_access_key_id:
                env["AWS_ACCESS_KEY_ID"] = settings.aws_access_key_id
            if settings.aws_secret_access_key:
                env["AWS_SECRET_ACCESS_KEY"] = settings.aws_secret_access_key
            if settings.aws_region:
                env["AWS_REGION"] = settings.aws_region
            return env

        if provider == "vertex":
            env = {
                "CLAUDE_CODE_USE_VERTEX": "1",
                "CLOUD_ML_REGION": settings.vertex_region or "us-east5",
            }
            if settings.vertex_project_id:
                env["ANTHROPIC_VERTEX_PROJECT_ID"] = settings.vertex_project_id
            if settings.vertex_credentials_json:
                # The agent entrypoint will write this to a file and set
                # GOOGLE_APPLICATION_CREDENTIALS accordingly.
                env["GOOGLE_CREDENTIALS_JSON"] = settings.vertex_credentials_json
            return env

        if provider == "foundry":
            env = {"CLAUDE_CODE_USE_FOUNDRY": "1"}
            if settings.foundry_api_key:
                env["ANTHROPIC_FOUNDRY_API_KEY"] = settings.foundry_api_key
            if settings.foundry_resource:
                env["ANTHROPIC_FOUNDRY_RESOURCE"] = settings.foundry_resource
            return env

        # Default: Anthropic Direct
        env = {}
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        elif settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token
        return env

    async def _publish_event(self, agent_id: str, event_type: str, message: str) -> None:
        """Publish a lifecycle event to the agent's log channel."""
        try:
            event = json.dumps({
                "agent_id": agent_id,
                "task_id": "",
                "type": event_type,
                "data": {"message": message},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            channel = f"agent:{agent_id}:logs"
            await self.redis.client.publish(channel, event)
            # Store in activity history (keep last 200 events)
            history_key = f"agent:{agent_id}:activity"
            await self.redis.client.rpush(history_key, event)
            await self.redis.client.ltrim(history_key, -200, -1)
        except Exception as e:
            logger.warning(f"Could not publish event for agent {agent_id}: {e}")

    async def _get_custom_mcp_env(self, agent_config: dict | None = None) -> dict[str, str]:
        """Load custom MCP servers and return as env var dict.

        If agent_config contains 'mcp_servers' (list of IDs), only those
        servers are included. Otherwise all enabled servers are returned.
        """
        result = await self.db.execute(
            select(McpServer).where(McpServer.enabled == True)
        )
        servers = result.scalars().all()
        if not servers:
            return {}

        # Per-agent filtering
        agent_mcp_ids = None
        if agent_config and "mcp_servers" in agent_config:
            agent_mcp_ids = set(agent_config["mcp_servers"])

        if agent_mcp_ids is not None:
            servers = [s for s in servers if s.id in agent_mcp_ids]

        if not servers:
            return {}
        mcp_map = {s.name: s.url for s in servers}
        return {"CUSTOM_MCP_SERVERS": json.dumps(mcp_map)}

    async def _get_integration_env(self, agent_integrations: list[str], user_id: str | None = None) -> dict[str, str]:
        """Get environment variables for agent integrations (e.g., GitHub token)."""
        env: dict[str, str] = {}
        if "github" in agent_integrations:
            result = await self.db.execute(
                select(OAuthIntegration).where(
                    OAuthIntegration.provider == OAuthProvider.GITHUB,
                    OAuthIntegration.user_id.is_(None),
                )
            )
            integration = result.scalar_one_or_none()
            if integration:
                token = decrypt_token(integration.access_token_encrypted)
                env["GITHUB_TOKEN"] = token
                env["GH_TOKEN"] = token
        if "microsoft" in agent_integrations and user_id:
            from sqlalchemy import and_ as _sqland
            result = await self.db.execute(
                select(OAuthIntegration).where(
                    _sqland(
                        OAuthIntegration.provider == OAuthProvider.MICROSOFT,
                        OAuthIntegration.user_id == user_id,
                    )
                )
            )
            if result.scalar_one_or_none():
                env["MSGRAPH_ENABLED"] = "true"
        return env

    async def create_agent(self, name: str, model: str | None = None, role: str | None = None, integrations: list[str] | None = None, permissions: list[str] | None = None, user_id: str | None = None, budget_usd: float | None = None, mode: str = "claude_code", llm_config: dict | None = None, browser_mode: bool = False, autonomy_level: str = "l3") -> Agent:
        agent_id = uuid.uuid4().hex[:8]
        container_name = f"ai-agent-{name.lower().replace(' ', '-')}-{agent_id}"
        volume_name = f"workspace-{agent_id}"
        session_volume = f"claude-session-{agent_id}"
        model = model or settings.default_model

        # Encrypt API key for custom_llm before storing
        encrypted_llm_config = None
        if mode == "custom_llm" and llm_config:
            from app.core.encryption import encrypt_token
            encrypted_llm_config = dict(llm_config)
            if encrypted_llm_config.get("api_key"):
                encrypted_llm_config["api_key_encrypted"] = encrypt_token(
                    encrypted_llm_config.pop("api_key")
                )
            # Use the custom model name as the display model
            model = llm_config.get("model_name", model)

        # Build environment based on mode
        env_vars: dict[str, str] = {
            "AGENT_ID": agent_id,
            "AGENT_NAME": name,
            "AGENT_ROLE": role or "",
            "AGENT_TOKEN": make_agent_token(agent_id),
            "REDIS_URL": settings.redis_url_internal,
            "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
            "AGENT_MODE": mode,
            "MAX_TURNS": str(settings.max_turns),
            "AUTONOMY_LEVEL": autonomy_level.lower(),
        }

        if mode == "custom_llm" and llm_config:
            # Custom LLM: LLM-specific env vars + integrations + MCP servers
            mcp_env = await self._get_custom_mcp_env()
            integration_env = await self._get_integration_env(integrations or [], user_id=user_id)
            env_vars.update({
                "LLM_PROVIDER_TYPE": llm_config["provider_type"],
                "LLM_API_ENDPOINT": llm_config["api_endpoint"],
                "LLM_API_KEY": llm_config["api_key"],
                "LLM_MODEL_NAME": llm_config["model_name"],
                "LLM_MAX_TOKENS": str(llm_config.get("max_tokens", 4096)),
                "LLM_TEMPERATURE": str(llm_config.get("temperature", 0.7)),
                "LLM_SYSTEM_PROMPT": llm_config.get("system_prompt", ""),
                "LLM_TOOLS_ENABLED": str(llm_config.get("tools_enabled", True)).lower(),
                "LLM_THINKING_MODE": llm_config.get("thinking_mode", "auto"),
                "DEFAULT_MODEL": llm_config["model_name"],
                **mcp_env,
                **integration_env,
            })
        else:
            # Claude Code: standard provider env + MCP + integrations
            # Per-agent provider from config, fallback to global
            agent_provider = None  # Will be set from DB config after agent is created
            provider_env = self._build_provider_env(agent_provider)
            mcp_env = await self._get_custom_mcp_env()
            integration_env = await self._get_integration_env(integrations or [], user_id=user_id)
            env_vars.update({
                **provider_env,
                **mcp_env,
                **integration_env,
                "DEFAULT_MODEL": model,
                "EXTENDED_THINKING": str(settings.extended_thinking).lower(),
                "COMPUTER_USE_BROWSER": "true" if browser_mode else "false",
                "COMPUTER_USE_USER_ID": str(user_id) if user_id else "",
                "AGENT_WORKSPACE_SIZE_GB": str(settings.agent_workspace_size_gb),
            })

        # Create Docker container with workspace + session + shared volumes
        agent_permissions = permissions if permissions is not None else DEFAULT_PERMISSIONS
        needs_sudo = len(agent_permissions) > 0
        container = self.docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment=env_vars,
            volume_name=volume_name,
            session_volume_name=session_volume,
            shared_volume_name="ai-employee-shared",
            network=settings.agent_network,
            memory_limit=settings.agent_memory_limit,
            cpu_quota=settings.agent_cpu_quota,
            needs_sudo=needs_sudo,
            bind_mounts=None,  # No mounts on initial create; assigned via PATCH /agents/{id}/mounts
        )

        # Apply permission packages (write sudoers file as root)
        try:
            self._apply_permissions(container.id, agent_permissions)
        except Exception as e:
            logger.warning(f"Could not apply permissions for agent {agent_id}: {e}")

        # Initialize workspace files
        claude_md = DEFAULT_CLAUDE_MD.replace(
            "$AGENT_WORKSPACE_SIZE_GB", str(settings.agent_workspace_size_gb)
        )
        if mode == "claude_code":
            # Claude Code: full CLAUDE.md + knowledge.md
            try:
                self.docker.write_file_in_container(
                    container.id, "/workspace/CLAUDE.md", claude_md
                )
                self.docker.write_file_in_container(
                    container.id, "/workspace/knowledge.md", DEFAULT_KNOWLEDGE_MD
                )
                logger.info(f"Initialized CLAUDE.md + knowledge.md for agent {agent_id}")
            except Exception as e:
                logger.warning(f"Could not initialize agent files: {e}")
        else:
            # Custom LLM: full workspace setup (same as Claude Code for parity)
            try:
                self.docker.write_file_in_container(
                    container.id, "/workspace/CLAUDE.md", claude_md
                )
                self.docker.write_file_in_container(
                    container.id, "/workspace/knowledge.md", DEFAULT_KNOWLEDGE_MD
                )
                logger.info(f"Initialized CLAUDE.md + knowledge.md for custom_llm agent {agent_id}")
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
            user_id=user_id,
            state=AgentState.RUNNING,
            model=model,
            mode=mode,
            llm_config=encrypted_llm_config,
            budget_usd=budget_usd,
            browser_mode=browser_mode,
            autonomy_level=autonomy_level.lower(),
            config={
                "session_volume": session_volume,
                "role": role or "",
                "onboarding_complete": False if mode == "claude_code" else True,
                "integrations": integrations or [],
                "permissions": agent_permissions,
                "agent_version": AGENT_VERSION,
                "metrics": {"total": 0, "success": 0, "fail": 0, "success_rate": 0.0},
            },
        )
        self.db.add(agent)
        await self.db.commit()
        await self.db.refresh(agent)

        # Create proactive schedule (auto-enabled, 1h default)
        try:
            now = datetime.now(timezone.utc)
            schedule_id = uuid.uuid4().hex[:8]
            proactive_schedule = Schedule(
                id=schedule_id,
                name=f"[Proactive] {name}",
                prompt=PROACTIVE_PROMPT,
                interval_seconds=3600,
                priority=0,
                agent_id=agent_id,
                enabled=True,
                next_run_at=now + timedelta(minutes=10),
            )
            self.db.add(proactive_schedule)

            config = dict(agent.config)
            config["proactive"] = {
                "enabled": True,
                "schedule_id": schedule_id,
                "interval_seconds": 3600,
            }
            agent.config = config
            flag_modified(agent, "config")
            await self.db.commit()
            await self.db.refresh(agent)
            logger.info(f"Created proactive schedule {schedule_id} for agent {agent_id}")
        except Exception as e:
            logger.warning(f"Could not create proactive schedule: {e}")

        await self._publish_event(agent_id, "system", f"Agent created: {name} (model: {model or 'default'})")
        return agent

    def _apply_permissions(self, container_id: str, permissions: list[str]) -> None:
        """Write sudoers file into container based on permission packages."""
        sudoers_content = generate_sudoers(permissions)
        if sudoers_content:
            # Write sudoers file via tar archive (avoids shell escaping issues)
            self.docker.write_file_in_container(
                container_id,
                "/etc/sudoers.d/agent-permissions",
                sudoers_content,
            )
            # Fix ownership and permissions (must be root:root, 0440)
            self.docker.exec_in_container(
                container_id,
                "chmod 0440 /etc/sudoers.d/agent-permissions",
                user="root",
            )
            logger.info(f"Applied permissions {permissions} to container {container_id[:12]}")
        else:
            # No permissions - remove any existing sudoers file
            self.docker.exec_in_container(
                container_id,
                "rm -f /etc/sudoers.d/agent-permissions",
                user="root",
            )
            logger.info(f"Removed all sudo permissions from container {container_id[:12]}")

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

    async def restart_agent(self, agent_id: str) -> Agent:
        """Restart agent by recreating its container with fresh env vars.

        Preserves all data (volumes, knowledge, config) but picks up
        new environment (MCP servers, integrations, etc).
        """
        await self._publish_event(agent_id, "system", "Agent restarting (recreating container with fresh config)...")
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

        # 2. Build fresh environment based on mode
        volume_name = agent.volume_name
        session_volume = config.get("session_volume", f"claude-session-{agent_id}")
        role = config.get("role", "")
        model = agent.model or settings.default_model
        container_name = f"ai-agent-{agent.name.lower().replace(' ', '-')}-{agent_id}"
        mode = agent.mode or "claude_code"

        env_vars: dict[str, str] = {
            "AGENT_ID": agent_id,
            "AGENT_NAME": agent.name,
            "AGENT_ROLE": role,
            "AGENT_TOKEN": make_agent_token(agent_id),
            "REDIS_URL": settings.redis_url_internal,
            "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
            "AGENT_MODE": mode,
            "MAX_TURNS": str(settings.max_turns),
            "AUTONOMY_LEVEL": (agent.autonomy_level or "l3").lower(),
        }

        if mode == "custom_llm" and agent.llm_config:
            from app.core.encryption import decrypt_token as _decrypt
            llm_cfg = agent.llm_config
            api_key = _decrypt(llm_cfg["api_key_encrypted"]) if llm_cfg.get("api_key_encrypted") else ""
            mcp_env = await self._get_custom_mcp_env(agent_config=config)
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                "LLM_PROVIDER_TYPE": llm_cfg.get("provider_type", ""),
                "LLM_API_ENDPOINT": llm_cfg.get("api_endpoint", ""),
                "LLM_API_KEY": api_key,
                "LLM_MODEL_NAME": llm_cfg.get("model_name", ""),
                "LLM_MAX_TOKENS": str(llm_cfg.get("max_tokens", 4096)),
                "LLM_TEMPERATURE": str(llm_cfg.get("temperature", 0.7)),
                "LLM_SYSTEM_PROMPT": llm_cfg.get("system_prompt", ""),
                "LLM_TOOLS_ENABLED": str(llm_cfg.get("tools_enabled", True)).lower(),
                "LLM_THINKING_MODE": llm_cfg.get("thinking_mode", "auto"),
                "DEFAULT_MODEL": llm_cfg.get("model_name", model),
                **mcp_env,
                **integration_env,
            })
        else:
            agent_provider = config.get("model_provider")
            provider_env = self._build_provider_env(agent_provider)
            mcp_env = await self._get_custom_mcp_env(agent_config=config)
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                **provider_env,
                **mcp_env,
                **integration_env,
                "DEFAULT_MODEL": model,
                "EXTENDED_THINKING": str(settings.extended_thinking).lower(),
            })

        # 3. Create new container with same volumes + any assigned bind mounts
        agent_permissions = config.get("permissions", DEFAULT_PERMISSIONS)
        needs_sudo = len(agent_permissions) > 0
        from app.core.mounts import parse_mount_catalog, resolve_agent_mounts, mounts_to_docker_volumes
        catalog = parse_mount_catalog(settings.agent_mount_catalog)
        mount_entries = resolve_agent_mounts(config.get("mounts", []), catalog)
        bind_mounts = mounts_to_docker_volumes(mount_entries) or None
        container = self.docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment=env_vars,
            volume_name=volume_name,
            session_volume_name=session_volume,
            shared_volume_name="ai-employee-shared",
            network=settings.agent_network,
            memory_limit=settings.agent_memory_limit,
            cpu_quota=settings.agent_cpu_quota,
            needs_sudo=needs_sudo,
            bind_mounts=bind_mounts,
        )

        # 4. Re-apply permissions
        try:
            self._apply_permissions(container.id, agent_permissions)
        except Exception as e:
            logger.warning(f"Could not apply permissions for agent {agent_id}: {e}")

        # 5. Update team registry
        try:
            self._update_team_registry(container.id, agent_id, agent.name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # 6. Update DB
        agent.container_id = container.id
        agent.state = AgentState.RUNNING
        await self.db.commit()
        await self.db.refresh(agent)

        logger.info(f"Agent {agent_id} restarted with fresh config")
        await self._publish_event(agent_id, "system", "Agent restarted successfully with updated config")
        return agent

    async def stop_agent(self, agent_id: str) -> Agent:
        await self._publish_event(agent_id, "system", "Agent stopping...")
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.stop_container(agent.container_id)
            except (NotFound, APIError) as e:
                logger.warning(f"Container {agent.container_id} not found when stopping agent {agent_id}: {e}")
        agent.state = AgentState.STOPPED
        await self.db.commit()
        await self._publish_event(agent_id, "system", "Agent stopped")
        return agent

    async def start_agent(self, agent_id: str) -> Agent:
        await self._publish_event(agent_id, "system", "Agent starting...")
        agent = await self._get_agent(agent_id)
        if not agent.container_id:
            # No container exists — recreate it (keeps volumes/data)
            logger.info(f"Agent {agent_id} has no container — recreating via update_agent")
            return await self.update_agent(agent_id)
        try:
            self.docker.start_container(agent.container_id)
        except NotFound:
            logger.warning(f"Container {agent.container_id} not found for agent {agent_id} — recreating")
            return await self.update_agent(agent_id)
        agent.state = AgentState.RUNNING
        await self.db.commit()
        await self._publish_event(agent_id, "system", "Agent started")
        return agent

    async def update_agent(self, agent_id: str) -> Agent:
        """Recreate agent container with latest image, preserving all data (volumes)."""
        agent = await self._get_agent(agent_id)
        config = agent.config or {}

        # 1. Stop and remove old container (volumes stay!)
        container_name = f"ai-agent-{agent.name.lower().replace(' ', '-')}-{agent_id}"
        # Try by container_id first, then by name (handles stale IDs)
        for ref in [agent.container_id, container_name]:
            if not ref:
                continue
            try:
                self.docker.stop_container(ref)
            except (NotFound, APIError):
                pass
            try:
                self.docker.remove_container(ref)
            except (NotFound, APIError):
                pass

        # 2. Build environment based on mode (same logic as restart)
        volume_name = agent.volume_name
        session_volume = config.get("session_volume", f"claude-session-{agent_id}")
        role = config.get("role", "")
        model = agent.model or settings.default_model
        mode = agent.mode or "claude_code"

        env_vars: dict[str, str] = {
            "AGENT_ID": agent_id,
            "AGENT_NAME": agent.name,
            "AGENT_ROLE": role,
            "AGENT_TOKEN": make_agent_token(agent_id),
            "REDIS_URL": settings.redis_url_internal,
            "ORCHESTRATOR_URL": "http://ai-employee-orchestrator:8000",
            "AGENT_MODE": mode,
            "MAX_TURNS": str(settings.max_turns),
            "AUTONOMY_LEVEL": (agent.autonomy_level or "l3").lower(),
        }

        if mode == "custom_llm" and agent.llm_config:
            from app.core.encryption import decrypt_token as _decrypt
            llm_cfg = agent.llm_config
            api_key = _decrypt(llm_cfg["api_key_encrypted"]) if llm_cfg.get("api_key_encrypted") else ""
            mcp_env = await self._get_custom_mcp_env(agent_config=config)
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                "LLM_PROVIDER_TYPE": llm_cfg.get("provider_type", ""),
                "LLM_API_ENDPOINT": llm_cfg.get("api_endpoint", ""),
                "LLM_API_KEY": api_key,
                "LLM_MODEL_NAME": llm_cfg.get("model_name", ""),
                "LLM_MAX_TOKENS": str(llm_cfg.get("max_tokens", 4096)),
                "LLM_TEMPERATURE": str(llm_cfg.get("temperature", 0.7)),
                "LLM_SYSTEM_PROMPT": llm_cfg.get("system_prompt", ""),
                "LLM_TOOLS_ENABLED": str(llm_cfg.get("tools_enabled", True)).lower(),
                "LLM_THINKING_MODE": llm_cfg.get("thinking_mode", "auto"),
                "DEFAULT_MODEL": llm_cfg.get("model_name", model),
                **mcp_env,
                **integration_env,
            })
        else:
            agent_provider = config.get("model_provider")
            provider_env = self._build_provider_env(agent_provider)
            mcp_env = await self._get_custom_mcp_env(agent_config=config)
            integration_env = await self._get_integration_env(config.get("integrations", []), user_id=agent.user_id)
            env_vars.update({
                **provider_env,
                **mcp_env,
                **integration_env,
                "DEFAULT_MODEL": model,
                "EXTENDED_THINKING": str(settings.extended_thinking).lower(),
            })

        # 3. Create new container with same volumes + any assigned bind mounts
        agent_permissions = config.get("permissions", DEFAULT_PERMISSIONS)
        needs_sudo = len(agent_permissions) > 0
        from app.core.mounts import parse_mount_catalog, resolve_agent_mounts, mounts_to_docker_volumes
        catalog = parse_mount_catalog(settings.agent_mount_catalog)
        mount_entries = resolve_agent_mounts(config.get("mounts", []), catalog)
        bind_mounts = mounts_to_docker_volumes(mount_entries) or None
        container = self.docker.create_container(
            image=settings.agent_image,
            name=container_name,
            environment=env_vars,
            volume_name=volume_name,
            session_volume_name=session_volume,
            shared_volume_name="ai-employee-shared",
            network=settings.agent_network,
            memory_limit=settings.agent_memory_limit,
            cpu_quota=settings.agent_cpu_quota,
            needs_sudo=needs_sudo,
            bind_mounts=bind_mounts,
        )

        # 4. Re-apply permission packages from config
        try:
            self._apply_permissions(container.id, agent_permissions)
        except Exception as e:
            logger.warning(f"Could not apply permissions for agent {agent_id}: {e}")

        # 5. Update workspace files (only CLAUDE.md for claude_code, knowledge preserved)
        if mode == "claude_code":
            try:
                self.docker.write_file_in_container(
                    container.id,
                    "/workspace/CLAUDE.md",
                    DEFAULT_CLAUDE_MD.replace("$AGENT_WORKSPACE_SIZE_GB", str(settings.agent_workspace_size_gb)),
                )
                logger.info(f"Updated CLAUDE.md for agent {agent_id} (knowledge.md preserved)")
            except Exception as e:
                logger.warning(f"Could not update CLAUDE.md: {e}")

        # 6. Update team registry
        try:
            self._update_team_registry(container.id, agent_id, agent.name, role or "Unassigned")
        except Exception as e:
            logger.warning(f"Could not update team registry: {e}")

        # 7. Update DB
        agent.container_id = container.id
        agent.state = AgentState.RUNNING
        config["agent_version"] = AGENT_VERSION
        agent.config = config
        flag_modified(agent, "config")
        await self.db.commit()
        await self.db.refresh(agent)

        logger.info(f"Agent {agent_id} updated to new image version")
        return agent

    async def update_llm_config(self, agent_id: str, updates: dict) -> dict:
        """Update LLM config fields for a custom_llm agent. Returns safe config (no key)."""
        from app.core.encryption import encrypt_token
        agent = await self._get_agent(agent_id)
        if agent.mode != "custom_llm":
            raise ValueError("Agent is not in custom_llm mode")

        llm_cfg = dict(agent.llm_config or {})

        # Handle API key update (encrypt it)
        if "api_key" in updates:
            llm_cfg["api_key_encrypted"] = encrypt_token(updates.pop("api_key"))

        # Merge other fields
        for key, value in updates.items():
            llm_cfg[key] = value

        agent.llm_config = llm_cfg
        flag_modified(agent, "llm_config")

        # Update model display name if model_name changed
        if "model_name" in updates:
            agent.model = updates["model_name"]

        await self.db.commit()
        await self.db.refresh(agent)

        # Restart container to pick up new config
        if agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            await self.restart_agent(agent_id)

        # Return safe config (no API key)
        return {
            "provider_type": llm_cfg.get("provider_type", ""),
            "api_endpoint": llm_cfg.get("api_endpoint", ""),
            "model_name": llm_cfg.get("model_name", ""),
            "max_tokens": llm_cfg.get("max_tokens", 4096),
            "temperature": llm_cfg.get("temperature", 0.7),
            "system_prompt": llm_cfg.get("system_prompt", ""),
            "tools_enabled": llm_cfg.get("tools_enabled", True),
        }

    async def remove_agent(self, agent_id: str, remove_data: bool = False) -> None:
        await self._publish_event(agent_id, "system", f"Agent being removed (delete data: {remove_data})")
        agent = await self._get_agent(agent_id)
        if agent.container_id:
            try:
                self.docker.remove_container(agent.container_id)
            except (NotFound, APIError) as e:
                logger.warning(f"Container {agent.container_id} already gone for agent {agent_id}: {e}")
        if remove_data and agent.volume_name:
            self.docker.remove_volume(agent.volume_name)
            config = agent.config or {}
            session_vol = config.get("session_volume")
            if session_vol:
                self.docker.remove_volume(session_vol)
        # Delete all schedules tied to this agent (FK constraint)
        await self.db.execute(
            sql_delete(Schedule).where(Schedule.agent_id == agent_id)
        )
        await self.db.delete(agent)
        await self.db.commit()

    async def get_agent_with_metrics(self, agent_id: str, include_stats: bool = True) -> dict:
        agent = await self._get_agent(agent_id)

        # Sync DB state with actual Docker container status (lightweight check).
        # Use a separate session for state updates because this method is called
        # concurrently via asyncio.gather() and the shared request session is not
        # safe for parallel commits (causes IllegalStateChangeError + destroys
        # SET LOCAL RLS settings).
        if agent.container_id:
            container_status = self.docker.get_container_status(agent.container_id)
            new_state = None
            if container_status == "running" and agent.state in (AgentState.ERROR, AgentState.STOPPED):
                new_state = AgentState.IDLE
            elif container_status == "unknown" and agent.state not in (AgentState.STOPPED, AgentState.ERROR):
                new_state = AgentState.ERROR
            elif container_status == "exited" and agent.state not in (AgentState.STOPPED,):
                new_state = AgentState.STOPPED

            if new_state is not None:
                from app.db.session import async_session_factory
                from sqlalchemy import text as sa_text
                async with async_session_factory() as sync_session:
                    await sync_session.execute(
                        sa_text(
                            f"UPDATE agents SET state = '{new_state.name}' "
                            f"WHERE id = '{agent_id}'"
                        )
                    )
                    await sync_session.commit()
                agent.state = new_state
                logger.info(f"Agent {agent_id} container status={container_status}, state→{new_state.name}")

        config = agent.config or {}

        # Check if agent version is outdated
        update_available = False
        stored_version = config.get("agent_version")
        if stored_version != AGENT_VERSION:
            update_available = True

        # Build safe LLM config for response (no API key!)
        llm_config_response = None
        if agent.mode == "custom_llm" and agent.llm_config:
            llm_cfg = agent.llm_config
            llm_config_response = {
                "provider_type": llm_cfg.get("provider_type", ""),
                "api_endpoint": llm_cfg.get("api_endpoint", ""),
                "model_name": llm_cfg.get("model_name", ""),
                "max_tokens": llm_cfg.get("max_tokens", 4096),
                "temperature": llm_cfg.get("temperature", 0.7),
                "system_prompt": llm_cfg.get("system_prompt", ""),
                "tools_enabled": llm_cfg.get("tools_enabled", True),
            }

        result = {
            "id": agent.id,
            "name": agent.name,
            "container_id": agent.container_id,
            "state": agent.state,
            "model": agent.model,
            "model_provider": (
                llm_config_response.get("provider_type", config.get("model_provider", settings.model_provider))
                if llm_config_response
                else config.get("model_provider", settings.model_provider)
            ),
            "mode": agent.mode or "claude_code",
            "llm_config": llm_config_response,
            "role": config.get("role", ""),
            "onboarding_complete": config.get("onboarding_complete", False),
            "integrations": config.get("integrations", []),
            "permissions": config.get("permissions", DEFAULT_PERMISSIONS),
            "update_available": update_available,
            "budget_usd": agent.budget_usd,
            "browser_mode": agent.browser_mode,
            "autonomy_level": agent.autonomy_level or "l3",
            "webhook_enabled": agent.webhook_enabled,
            "webhook_token": agent.webhook_token,
            "total_cost_usd": config.get("total_cost_usd", 0.0),
            "user_id": agent.user_id,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
            "knowledge_template": config.get("knowledge_template", ""),
            "config": config,
        }

        # Add live metrics from Redis
        status = await self.redis.get_agent_status(agent_id)
        result["current_task"] = status.get("current_task", "")
        result["queue_depth"] = await self.redis.get_queue_depth(agent_id)

        # Sync live state from Redis (agent reports idle/working in real-time)
        redis_state = status.get("state", "")
        if redis_state in ("idle", "working") and agent.state in (
            AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING
        ):
            result["state"] = redis_state

        # Add Docker stats if running (run in thread pool to avoid blocking)
        if include_stats and agent.container_id and agent.state in (AgentState.RUNNING, AgentState.IDLE, AgentState.WORKING):
            loop = asyncio.get_event_loop()
            try:
                stats = await loop.run_in_executor(
                    None, self.docker.get_container_stats, agent.container_id
                )
                result["cpu_percent"] = stats["cpu_percent"]
                result["memory_usage_mb"] = stats["memory_usage_mb"]
            except Exception:
                result["cpu_percent"] = None
                result["memory_usage_mb"] = None
            try:
                per_agent_quota = float(agent.config.get("workspace_size_gb") or settings.agent_workspace_size_gb) if agent.config else settings.agent_workspace_size_gb
                disk = await loop.run_in_executor(
                    None,
                    self.docker.get_workspace_disk_usage,
                    agent.container_id,
                    per_agent_quota,
                )
                result["disk_usage_mb"] = disk.get("disk_usage_mb")
                result["disk_limit_mb"] = disk.get("disk_limit_mb")
                result["disk_percent"] = disk.get("disk_percent")
            except Exception:
                result["disk_usage_mb"] = None
                result["disk_limit_mb"] = None
                result["disk_percent"] = None

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
