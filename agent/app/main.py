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
    builtin_servers = {
        "memory": {
            "command": "node",
            "args": ["/opt/mcp/memory-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
            },
        },
        "notifications": {
            "command": "node",
            "args": ["/opt/mcp/notification-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
            },
        },
        "orchestrator": {
            "command": "node",
            "args": ["/opt/mcp/orchestrator-server.mjs"],
            "env": {
                "ORCHESTRATOR_URL": settings.orchestrator_url,
                "AGENT_ID": settings.agent_id,
                "AGENT_NAME": settings.agent_name or settings.agent_id,
                "DEFAULT_MODEL": settings.default_model,
            },
        },
    }

    for name, cfg in builtin_servers.items():
        env_args: list[str] = []
        for k, v in cfg["env"].items():
            env_args.extend(["-e", f"{k}={v}"])
        cmd_args = env_args + [name, "--", cfg["command"]] + cfg["args"]
        if _run_mcp_add(cmd_args):
            print(f"[Agent] Registered MCP server: {name} (stdio)")
        else:
            print(f"[Agent] WARN: Failed to register MCP server: {name}")

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

    # Built-in servers
    for name, cmd, envs in [
        ("memory", "/opt/mcp/memory-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id}),
        ("notifications", "/opt/mcp/notification-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id}),
        ("orchestrator", "/opt/mcp/orchestrator-server.mjs", {"ORCHESTRATOR_URL": settings.orchestrator_url, "AGENT_ID": settings.agent_id, "AGENT_NAME": settings.agent_name or settings.agent_id, "DEFAULT_MODEL": settings.default_model}),
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


async def main() -> None:
    agent_id = settings.agent_id
    print(f"[Agent {agent_id}] Starting up...")

    # Set up Vertex AI credentials if provided
    setup_vertex_credentials()

    # Register MCP servers with Claude Code CLI
    register_mcp_servers()
    print(f"[Agent {agent_id}] MCP servers registered")

    # Start health server
    health_runner = await start_health_server(agent_id, settings.health_port)
    print(f"[Agent {agent_id}] Health server on port {settings.health_port}")

    # Start task consumer
    task_consumer = TaskConsumer(agent_id)

    # Start chat consumer
    chat_consumer = ChatConsumer(agent_id)

    # Graceful shutdown on SIGTERM/SIGINT
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(
                shutdown(task_consumer, chat_consumer, health_runner)
            ),
        )

    print(f"[Agent {agent_id}] Listening for tasks on queue agent:{agent_id}:tasks")
    print(f"[Agent {agent_id}] Listening for chat on queue agent:{agent_id}:chat")

    # Run both consumers concurrently
    await asyncio.gather(
        task_consumer.start(),
        chat_consumer.start(),
    )


async def shutdown(task_consumer: TaskConsumer, chat_consumer: ChatConsumer, health_runner) -> None:
    print("[Agent] Shutting down gracefully...")
    await task_consumer.stop()
    await chat_consumer.stop()
    await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
