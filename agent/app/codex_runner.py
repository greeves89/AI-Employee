"""Codex CLI runner.

Runs OpenAI Codex through the CLI using a ChatGPT/Codex auth.json mounted by
the orchestrator into /shared/.codex/auth.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import AsyncIterator

from app.config import settings
from app.log_publisher import LogPublisher
from app.runner_hooks import (
    SELF_IMPROVEMENT_SUFFIX,
    compose_prompt_bundle,
)

logger = logging.getLogger(__name__)


def _extract_text(event: dict) -> str:
    """Best-effort text extraction across Codex JSONL event shapes."""
    for key in ("text", "message", "delta", "result", "output_text"):
        value = event.get(key)
        if isinstance(value, str):
            return value

    payload = event.get("payload")
    if isinstance(payload, dict):
        text = _extract_text(payload)
        if text:
            return text

    item = event.get("item")
    if isinstance(item, dict):
        text = _extract_text(item)
        if text:
            return text

    message = event.get("message")
    if isinstance(message, dict):
        text = _extract_text(message)
        if text:
            return text
        content = message.get("content")
        if isinstance(content, list):
            return _extract_content_text(content)

    content = event.get("content")
    if isinstance(content, list):
        return _extract_content_text(content)

    return ""


def _extract_content_text(content: list) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            value = (
                block.get("text")
                or block.get("output_text")
                or block.get("input_text")
            )
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts)


def _event_item(event: dict) -> dict:
    """Return the nested Codex item/payload if present."""
    item = event.get("item")
    if isinstance(item, dict):
        return item
    payload = event.get("payload")
    if isinstance(payload, dict):
        return payload
    return event


def _extract_tool_call(event: dict) -> tuple[str, dict, str] | None:
    item = _event_item(event)
    event_type = str(event.get("type", "")).lower()
    typ = str(item.get("type", event.get("type", ""))).lower()
    status = str(item.get("status", "")).lower()

    if event_type.endswith("completed") or status in {"completed", "failed"}:
        return None

    if typ == "command_execution":
        command = item.get("command") or item.get("cmd") or ""
        return "bash", {"cmd": str(command)}, str(item.get("id") or event.get("id") or "")

    if "tool" not in typ and "function_call" not in typ:
        return None
    name = item.get("name") or item.get("tool") or item.get("call_name")
    if not name:
        return None
    args = item.get("arguments") or item.get("input") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    return (
        str(name),
        args if isinstance(args, dict) else {"raw": args},
        str(item.get("id") or event.get("id") or ""),
    )


def _extract_tool_result(event: dict) -> tuple[str, str] | None:
    item = _event_item(event)
    event_type = str(event.get("type", "")).lower()
    typ = str(item.get("type", "")).lower()
    status = str(item.get("status", "")).lower()

    if not (event_type.endswith("completed") or status in {"completed", "failed"}):
        return None

    if typ == "command_execution":
        output = item.get("aggregated_output")
        if output is None:
            output = item.get("output") or ""
        exit_code = item.get("exit_code")
        if exit_code is not None:
            output = f"{output}\n(exit code: {exit_code})" if output else f"(exit code: {exit_code})"
        return str(item.get("id") or event.get("id") or ""), str(output)

    if "tool" in typ or "function_call" in typ:
        result = item.get("result") or item.get("output") or item.get("content") or ""
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        return str(item.get("id") or event.get("id") or ""), result

    return None


class CodexAgentRunner:
    """Executes tasks through `codex exec --json`."""

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self._process: asyncio.subprocess.Process | None = None
        self._runner: CodexAgentRunner | None = None
        self.is_running = False

    async def execute_task(
        self, task_id: str, prompt: str, model: str | None = None,
        lightweight: bool = False,
    ) -> dict:
        model = model or settings.default_model
        self.is_running = True

        task_id_line = f"CURRENT_TASK_ID: {task_id}\n\n"
        # Unified context bundle (shared via runner_hooks) — same blocks as the
        # Claude and custom_llm runtimes, incl. host mounts / Second Brain awareness.
        enhanced_prompt = (
            task_id_line
            + compose_prompt_bundle(prompt, lightweight)
            + prompt
            + SELF_IMPROVEMENT_SUFFIX
        )

        await self.log_publisher.publish(
            task_id, "system", {"message": f"Starting Codex task with model {model}"}
        )
        result = await self._run_codex(task_id, enhanced_prompt, model, stream="task")
        self.is_running = False
        self._process = None
        return result

    async def _run_codex(self, target_id: str, prompt: str, model: str, stream: str) -> dict:
        # Prompt via STDIN ("-") not argv → avoids E2BIG ("Argument list too long")
        # on large prompts (PR diffs etc.), same reason the claude path pipes stdin.
        cmd = [
            "codex", "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C", settings.workspace_dir,
            "-m", model,
            "-",
        ]
        env = _codex_env()

        stderr_lines: list[str] = []
        text_output: list[str] = []
        result_data: dict = {"status": "completed", "result": ""}
        completed_seen = False

        async def collect_stderr(proc: asyncio.subprocess.Process) -> None:
            if not proc.stderr:
                return
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    logger.warning("[Codex stderr] %s", decoded)

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )
            # Feed the prompt via stdin, then close so codex starts processing.
            if self._process.stdin is not None:
                self._process.stdin.write(prompt.encode("utf-8"))
                await self._process.stdin.drain()
                self._process.stdin.close()
            stderr_task = asyncio.create_task(collect_stderr(self._process))

            async for event in _stream_jsonl(self._process):
                text = _extract_text(event)
                if text:
                    text_output.append(text)
                    await _publish(self.log_publisher, stream, target_id, "text", {"text": text})

                tool_call = _extract_tool_call(event)
                if tool_call:
                    name, args, tool_id = tool_call
                    await _publish(
                        self.log_publisher,
                        stream,
                        target_id,
                        "tool_call",
                        {"tool_use_id": tool_id, "tool": name, "input": args},
                    )

                tool_result = _extract_tool_result(event)
                if tool_result:
                    tool_id, output = tool_result
                    await _publish(
                        self.log_publisher,
                        stream,
                        target_id,
                        "tool_result",
                        {"tool_use_id": tool_id, "content": output},
                    )

                if str(event.get("type", "")).endswith("completed"):
                    completed_seen = True
                    usage = event.get("usage", {}) if isinstance(event.get("usage"), dict) else {}
                    result_data.update({
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                    })

            returncode = await self._process.wait()
            await stderr_task
            final_text = "".join(text_output)
            result_data["result"] = final_text
            result_data["text"] = final_text

            if returncode != 0:
                stderr_text = "\n".join(stderr_lines).strip()
                # The Codex CLI runs with stdin=DEVNULL: after finishing its turn it
                # tries to read more input, hits EOF and prints "Reading additional
                # input from stdin..." then exits non-zero. That is NOT a task
                # failure. Treat the run as successful when a completion event was
                # seen (or the only issue is that benign stdin-EOF after real
                # output); report an error only for a run that genuinely produced
                # neither a completion nor any output.
                benign_stdin = "reading additional input from stdin" in stderr_text.lower()
                if not (completed_seen or (benign_stdin and final_text.strip())):
                    error = stderr_text or f"Codex CLI exited with code {returncode}"
                    result_data = {"status": "error", "error": error}
                    await _publish(self.log_publisher, stream, target_id, "error", {"message": error})
        except asyncio.CancelledError:
            await self.interrupt()
            result_data = {"status": "cancelled"}
        except Exception as e:
            result_data = {"status": "error", "error": str(e)}
            await _publish(self.log_publisher, stream, target_id, "error", {"message": str(e)})

        return result_data

    async def interrupt(self) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()


class CodexChatHandler:
    """Handles chat messages through Codex CLI."""

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self._process: asyncio.subprocess.Process | None = None
        self.is_running = False

    async def handle_message(self, message_id: str, text: str, model: str | None = None) -> dict:
        self.is_running = True
        model = model or settings.default_model
        self._runner = CodexAgentRunner(self.log_publisher)
        result = await self._runner._run_codex(message_id, text, model, stream="chat")
        await self.log_publisher.publish_chat(message_id, "done", result)
        self.is_running = False
        self._runner = None
        self._process = None
        return result

    async def stop_current(self) -> None:
        if self._runner:
            await self._runner.interrupt()

    async def reset_session(self) -> None:
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Codex chat session reset"}
        )


def _codex_env() -> dict[str, str]:
    env = os.environ.copy()
    codex_home = env.setdefault("CODEX_HOME", "/home/agent/.codex")
    _ensure_codex_mcp_config(codex_home, env)
    return env


_SAFE_MCP_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]+$")


def _toml_escape(value: str) -> str:
    """Escape a value for embedding in a TOML double-quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _valid_http_url(url: str) -> bool:
    import urllib.parse
    try:
        p = urllib.parse.urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc) and "\n" not in url
    except Exception:
        return False


def _ensure_codex_mcp_config(codex_home: str, env: dict) -> None:
    """Write ~/.codex/config.toml with built-in + custom MCP servers.

    Called once per Codex invocation. Idempotent — overwrites the server
    sections so new agents or updated CUSTOM_MCP_SERVERS are always current.
    """
    import pathlib

    # Restrict directory to owner-only — file will contain AGENT_TOKEN
    os.makedirs(codex_home, mode=0o700, exist_ok=True)
    config_path = pathlib.Path(codex_home) / "config.toml"

    orch_url = env.get("ORCHESTRATOR_URL", "http://ai-employee-orchestrator:8000")
    if not _valid_http_url(orch_url):
        orch_url = "http://ai-employee-orchestrator:8000"
    agent_token = env.get("AGENT_TOKEN", "")
    agent_id = env.get("AGENT_ID", "")
    agent_name = env.get("AGENT_NAME", "") or agent_id
    default_model = env.get("DEFAULT_MODEL", "")

    # Built-in AI Employee MCP servers (stdio)
    builtin_servers = {
        "brain": "/opt/mcp/brain-server.mjs",
        "skill": "/opt/mcp/skill-server.mjs",
        "memory": "/opt/mcp/memory-server.mjs",
        "notification": "/opt/mcp/notification-server.mjs",
        "orchestrator": "/opt/mcp/orchestrator-server.mjs",
        "read_logs": "/opt/mcp/read-logs-server.mjs",
    }

    lines: list[str] = [
        '[projects."/workspace"]',
        'trust_level = "trusted"',
        "",
    ]

    # Stdio built-in servers
    for name, script in builtin_servers.items():
        if not os.path.exists(script):
            continue
        # Codex only exposes the env vars declared in this [env] block to the
        # MCP server — it does NOT inherit the agent container's environment.
        # The built-in servers authenticate to the orchestrator with the agent
        # HMAC token, which is keyed on AGENT_ID; if AGENT_ID is missing the
        # .mjs servers fall back to "unknown" and every call is rejected (401).
        server_env = [
            f"[mcp_servers.{name}.env]",
            f'ORCHESTRATOR_URL = "{_toml_escape(orch_url)}"',
            f'AGENT_ID = "{_toml_escape(agent_id)}"',
            f'AGENT_TOKEN = "{_toml_escape(agent_token)}"',
        ]
        if name == "orchestrator":
            server_env += [
                f'AGENT_NAME = "{_toml_escape(agent_name)}"',
                f'DEFAULT_MODEL = "{_toml_escape(default_model)}"',
            ]
        lines += [
            f"[mcp_servers.{name}]",
            'command = "node"',
            f'args = ["{script}"]',
            "",
            *server_env,
            "",
        ]

    # Custom HTTP MCP servers from CUSTOM_MCP_SERVERS env var
    custom_raw = env.get("CUSTOM_MCP_SERVERS", "")
    auth_raw = env.get("CUSTOM_MCP_AUTH", "")
    if custom_raw:
        try:
            custom_servers: dict = json.loads(custom_raw)
            auth_map: dict = json.loads(auth_raw) if auth_raw else {}
            for srv_name, srv_url in custom_servers.items():
                safe_name = srv_name.replace("-", "_").replace(" ", "_")
                if not _SAFE_MCP_NAME_RE.match(safe_name):
                    logger.warning("Skipping MCP server with unsafe name: %r", srv_name)
                    continue
                if not _valid_http_url(srv_url):
                    logger.warning("Skipping MCP server %r with invalid URL: %r", srv_name, srv_url)
                    continue
                lines += [
                    f"[mcp_servers.{safe_name}]",
                    f'url = "{_toml_escape(srv_url)}"',
                ]
                if srv_name in auth_map:
                    token_env_var = f"MCP_TOKEN_{safe_name}"
                    env[token_env_var] = auth_map[srv_name]
                    lines.append(f'bearer_token_env_var = "{token_env_var}"')
                lines.append("")
        except Exception as e:
            logger.warning("Failed to parse CUSTOM_MCP_SERVERS for Codex config: %s", e)

    # Write with owner-only permissions (0o600) — contains AGENT_TOKEN
    data = "\n".join(lines).encode()
    fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    logger.debug("Written Codex MCP config to %s (%d built-in servers)", config_path, len(builtin_servers))


async def _publish(
    publisher: LogPublisher, stream: str, target_id: str, event_type: str, payload: dict
) -> None:
    if stream == "chat":
        await publisher.publish_chat(target_id, event_type, payload)
    else:
        await publisher.publish(target_id, event_type, payload)


async def _stream_jsonl(process: asyncio.subprocess.Process) -> AsyncIterator[dict]:
    if not process.stdout:
        return
    buffer = b""
    while True:
        chunk = await process.stdout.read(4096)
        if not chunk:
            if buffer.strip():
                for line in buffer.decode("utf-8", errors="replace").splitlines():
                    parsed = _parse_line(line)
                    if parsed:
                        yield parsed
            break
        buffer += chunk
        while b"\n" in buffer:
            line_bytes, buffer = buffer.split(b"\n", 1)
            parsed = _parse_line(line_bytes.decode("utf-8", errors="replace"))
            if parsed:
                yield parsed


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"type": "raw", "text": line}
