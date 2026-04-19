import asyncio
import json
import os
import re
import signal
import subprocess

from app.config import settings
from app.health import start_health_server
from app.task_consumer import TaskConsumer
from app.chat_consumer import ChatConsumer
from app.message_consumer import MessageConsumer


async def discover_custom_mcp_tools() -> int:
    """Discover tools from custom MCP HTTP servers for custom_llm mode.

    Adds discovered tools to TOOL_DEFINITIONS so the LLM can use them.
    Returns the number of tools discovered.
    """
    from app.tools.mcp_client import MCPHTTPClient
    from app.tools.definitions import TOOL_DEFINITIONS

    client = MCPHTTPClient()
    try:
        tools = await client.discover_tools()
        if tools:
            TOOL_DEFINITIONS.extend(tools)
        return len(tools)
    except Exception as e:
        print(f"[Agent] Warning: MCP tool discovery failed: {e}")
        return 0
    finally:
        await client.close()


def _sanitize_mcp_name(name: str) -> str:
    """Sanitize MCP server name: only letters, numbers, hyphens, underscores."""
    return re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-")


def _run_mcp_add(args: list[str]) -> bool:
    """Run `claude mcp add` with given args. Returns True on success."""
    cmd = ["claude", "mcp", "add", "--scope", "user"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True
        print(f"[Agent] claude mcp add failed: {result.stderr.strip()}")
        return False
    except Exception as e:
        print(f"[Agent] claude mcp add error: {e}")
        return False


def register_mcp_servers() -> None:
    """Register MCP servers via `claude mcp add` so Claude Code discovers them in -p mode.

    .mcp.json alone is NOT sufficient — Claude Code headless mode requires servers
    to be registered via `claude mcp add --scope user`.
    """
    # Built-in stdio servers
    # AGENT_TOKEN is required for all API calls (verify_agent_token auth)
    builtin_servers = {
        "bash-approval": {
            "command": "node",
            "args": ["/opt/mcp/bash-approval-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
            },
        },
        "memory": {
            "command": "node",
            "args": ["/opt/mcp/memory-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
            },
        },
        "notifications": {
            "command": "node",
            "args": ["/opt/mcp/notification-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
            },
        },
        "orchestrator": {
            "command": "node",
            "args": ["/opt/mcp/orchestrator-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_NAME": settings.agent_name or settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
                "DEFAULT_MODEL": settings.default_model,
            },
        },
        "knowledge": {
            "command": "node",
            "args": ["/opt/mcp/knowledge-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
            },
        },
        "skills": {
            "command": "node",
            "args": ["/opt/mcp/skill-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
            },
        },
        "computer-use": {
            "command": "node",
            "args": ["/opt/mcp/computer-use-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_TOKEN": settings.agent_token,
            },
        },
    }

    for name, cfg in builtin_servers.items():
        env_args: list[str] = []
        for k, v in cfg["env"].items():
            env_args.extend(["-e", f"{k}={v}"])
        cmd_args = env_args + ["--", name, cfg["command"]] + cfg["args"]
        if _run_mcp_add(cmd_args):
            print(f"[Agent] Registered MCP server: {name} (stdio)")
        else:
            print(f"[Agent] WARN: Failed to register MCP server: {name}")

    # Playwright MCP — browser automation (enabled via COMPUTER_USE_BROWSER=true)
    if os.environ.get("COMPUTER_USE_BROWSER", "").lower() == "true":
        if _run_mcp_add(["playwright", "npx", "@playwright/mcp@latest"]):
            print("[Agent] Registered Playwright MCP server (browser control enabled)")
        else:
            print("[Agent] WARN: Failed to register Playwright MCP server")

    # Computer-Use Desktop Bridge MCP — local desktop control via bridge app
    bridge_url = os.environ.get("COMPUTER_USE_BRIDGE_MCP_URL", "")
    if bridge_url:
        safe = _sanitize_mcp_name("computer-use-bridge")
        if _run_mcp_add(["--transport", "http", safe, bridge_url]):
            print(f"[Agent] Registered Computer-Use Bridge MCP: {bridge_url}")
        else:
            print("[Agent] WARN: Failed to register Computer-Use Bridge MCP")

    # Custom HTTP servers from env (passed by orchestrator)
    custom_mcp = os.environ.get("CUSTOM_MCP_SERVERS", "")
    if custom_mcp:
        try:
            servers = json.loads(custom_mcp)
            for name, url in servers.items():
                safe_name = _sanitize_mcp_name(name)
                cmd_args = ["--transport", "http", safe_name, url]
                if _run_mcp_add(cmd_args):
                    print(f"[Agent] Registered MCP server: {safe_name} -> {url} (http)")
                else:
                    print(f"[Agent] WARN: Failed to register MCP server: {safe_name}")
        except (json.JSONDecodeError, AttributeError) as e:
            print(f"[Agent] Warning: Could not parse CUSTOM_MCP_SERVERS: {e}")

    # Also write .mcp.json as fallback / documentation
    _write_mcp_json_fallback()


def _write_mcp_json_fallback() -> None:
    """Write .mcp.json as fallback for interactive mode / documentation."""
    mcp_config: dict = {"mcpServers": {}}

    # Built-in servers (AGENT_TOKEN required for API auth)
    for name, cmd, envs in [
        ("bash-approval", "/opt/mcp/bash-approval-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_TOKEN": settings.agent_token}),
        ("memory", "/opt/mcp/memory-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_TOKEN": settings.agent_token}),
        ("notifications", "/opt/mcp/notification-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_TOKEN": settings.agent_token}),
        ("orchestrator", "/opt/mcp/orchestrator-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_NAME": settings.agent_name or settings.agent_id, "AGENT_TOKEN": settings.agent_token, "DEFAULT_MODEL": settings.default_model}),
        ("knowledge", "/opt/mcp/knowledge-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_TOKEN": settings.agent_token}),
        ("skills", "/opt/mcp/skill-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_TOKEN": settings.agent_token}),
        ("computer-use", "/opt/mcp/computer-use-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_TOKEN": settings.agent_token}),
    ]:
        mcp_config["mcpServers"][name] = {"command": "node", "args": [cmd], "env": envs}

    # Custom servers
    custom_mcp = os.environ.get("CUSTOM_MCP_SERVERS", "")
    if custom_mcp:
        try:
            for name, url in json.loads(custom_mcp).items():
                mcp_config["mcpServers"][_sanitize_mcp_name(name)] = {"url": url}
        except (json.JSONDecodeError, AttributeError):
            pass

    config_path = os.path.join(settings.workspace_dir, ".mcp.json")
    with open(config_path, "w") as f:
        json.dump(mcp_config, f, indent=2)


def setup_github_credentials() -> None:
    """If GITHUB_TOKEN env is set, configure git and gh CLI for authentication."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return

    agent_name = settings.agent_name or settings.agent_id

    # Configure git to use token for HTTPS GitHub access
    subprocess.run(
        ["git", "config", "--global", "url.https://oauth2:{t}@github.com/.insteadOf".format(t=token),
         "https://github.com/"],
        capture_output=True, text=True,
    )
    subprocess.run(["git", "config", "--global", "user.name", agent_name], capture_output=True, text=True)
    subprocess.run(["git", "config", "--global", "user.email", f"{settings.agent_id}@ai-employee.local"], capture_output=True, text=True)

    # gh CLI uses GH_TOKEN env var automatically (set by orchestrator)
    print(f"[Agent] GitHub credentials configured for {agent_name}")


def setup_vertex_credentials() -> None:
    """If GOOGLE_CREDENTIALS_JSON env is set, write it to a file and point
    GOOGLE_APPLICATION_CREDENTIALS at it (needed for Vertex AI provider)."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        return
    creds_path = "/tmp/gcp-credentials.json"
    with open(creds_path, "w") as f:
        f.write(creds_json)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    print(f"[Agent] Wrote GCP credentials to {creds_path}")


def _copy_skill_to_workspace(skill_name: str, target_dir: str) -> None:
    """Find a skill installed by npx in /tmp and copy it to the workspace skills dir."""
    import glob
    import shutil

    # npx skills add puts files in /tmp/skills-*/
    patterns = [
        f"/tmp/skills-*/.claude/skills/{skill_name}",
        f"/tmp/skills-*/skills/{skill_name}",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            os.makedirs(os.path.dirname(target_dir), exist_ok=True)
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            shutil.copytree(matches[0], target_dir)
            return


def setup_default_skills() -> None:
    """Install default Claude Code skills if not already present.

    These skills enhance agent capabilities across all agent types.
    Failures are non-critical — agents can install them manually later.
    """
    skills_dir = os.path.join(settings.workspace_dir, ".claude", "skills")
    default_skills = [
        {
            "repo": "https://github.com/vercel-labs/skills",
            "skill": "find-skills",
            "check_dir": os.path.join(skills_dir, "find-skills"),
        },
        {
            "repo": "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
            "skill": "ui-ux-pro-max",
            "check_dir": os.path.join(skills_dir, "ui-ux-pro-max"),
        },
    ]

    for skill_info in default_skills:
        if os.path.isdir(skill_info["check_dir"]):
            print(f"[Agent] Skill already installed: {skill_info['skill']}")
            continue
        try:
            result = subprocess.run(
                ["npx", "-y", "skills", "add", skill_info["repo"], "--skill", skill_info["skill"]],
                capture_output=True, text=True, timeout=60,
                cwd=settings.workspace_dir,
            )
            if result.returncode == 0:
                # npx skills installs to a temp dir — find and copy to workspace
                _copy_skill_to_workspace(skill_info["skill"], skill_info["check_dir"])
                print(f"[Agent] Installed skill: {skill_info['skill']}")
            else:
                print(f"[Agent] Skill install failed ({skill_info['skill']}): {result.stderr.strip()[:200]}")
        except Exception as e:
            print(f"[Agent] Skill install error ({skill_info['skill']}): {e}")


async def bootstrap_knowledge() -> None:
    """Fetch agent info from orchestrator and create /workspace/knowledge.md if missing.

    This ensures the agent always starts with its template knowledge available
    as a local file that startup instructions reference.
    """
    knowledge_path = os.path.join(settings.workspace_dir, "knowledge.md")
    if os.path.exists(knowledge_path):
        print(f"[Agent] knowledge.md already exists, skipping bootstrap")
        return

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            # Fetch agent details (includes template knowledge)
            url = f"{settings.orchestrator_url}/api/v1/agents/{settings.agent_id}"
            resp = await client.get(url, headers={"X-Agent-Token": settings.agent_token})
            if resp.status_code != 200:
                print(f"[Agent] Could not fetch agent info: {resp.status_code}")
                return
            data = resp.json()
            knowledge = data.get("knowledge_template") or data.get("knowledge", "")
            if not knowledge:
                knowledge = f"## Role: {data.get('name', settings.agent_id)}\n\n{data.get('role', '')}\n"

            # Also fetch improvement data if available
            config = data.get("config") or {}
            improvement = config.get("improvement")
            if improvement:
                knowledge += (
                    f"\n\n## Performance Metrics\n"
                    f"- Average Rating: {improvement.get('average_rating', 'N/A')}/5\n"
                    f"- Status: {improvement.get('status', 'unknown')}\n"
                    f"- Total Ratings: {improvement.get('total_ratings', 0)}\n"
                )
                top_issues = improvement.get("top_issues", [])
                if top_issues:
                    knowledge += "- Top Issues from User Feedback:\n"
                    for issue in top_issues:
                        knowledge += f"  - {issue}\n"

            with open(knowledge_path, "w") as f:
                f.write(knowledge)
            print(f"[Agent] Created knowledge.md from template ({len(knowledge)} chars)")
    except Exception as e:
        print(f"[Agent] Warning: knowledge.md bootstrap failed: {e}")


async def main() -> None:
    agent_id = settings.agent_id
    mode = settings.agent_mode
    print(f"[Agent {agent_id}] Starting up (mode: {mode})...")

    if mode == "custom_llm":
        # Custom LLM mode: skip Claude CLI setup, but set up integrations
        print(f"[Agent {agent_id}] Custom LLM: {settings.llm_provider_type}/{settings.llm_model_name}")
        print(f"[Agent {agent_id}] Tools enabled: {settings.llm_tools_enabled}")

        # GitHub credentials (needed for proactive mode git operations)
        setup_github_credentials()

        # Discover custom MCP server tools (if any configured)
        mcp_tool_count = await discover_custom_mcp_tools()
        if mcp_tool_count > 0:
            print(f"[Agent {agent_id}] Discovered {mcp_tool_count} custom MCP tools")

        # Built-in orchestrator API tools are always available
        from app.tools.definitions import ORCHESTRATOR_TOOL_NAMES
        print(f"[Agent {agent_id}] {len(ORCHESTRATOR_TOOL_NAMES)} orchestrator API tools available")
    else:
        # Claude Code mode: full setup (MCP servers, credentials, etc.)
        setup_vertex_credentials()
        setup_github_credentials()
        register_mcp_servers()
        print(f"[Agent {agent_id}] MCP servers registered")
        setup_default_skills()
        print(f"[Agent {agent_id}] Default skills checked")

    # Bootstrap knowledge.md from template (if not exists)
    await bootstrap_knowledge()

    # Start health server
    health_runner = await start_health_server(agent_id, settings.health_port)
    print(f"[Agent {agent_id}] Health server on port {settings.health_port}")

    # Start consumers
    task_consumer = TaskConsumer(agent_id)
    chat_consumer = ChatConsumer(agent_id)
    message_consumer = MessageConsumer(agent_id)

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(
                shutdown(task_consumer, chat_consumer, message_consumer, health_runner)
            ),
        )

    print(f"[Agent {agent_id}] Listening for tasks on queue agent:{agent_id}:tasks")
    print(f"[Agent {agent_id}] Listening for chat on queue agent:{agent_id}:chat")
    print(f"[Agent {agent_id}] Listening for messages on queue agent:{agent_id}:messages")

    # Run all three consumers concurrently
    await asyncio.gather(
        task_consumer.start(),
        chat_consumer.start(),
        message_consumer.start(),
    )


async def shutdown(task_consumer: TaskConsumer, chat_consumer: ChatConsumer, message_consumer: MessageConsumer, health_runner) -> None:
    print("[Agent] Shutting down gracefully...")
    await task_consumer.stop()
    await chat_consumer.stop()
    await message_consumer.stop()
    await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
