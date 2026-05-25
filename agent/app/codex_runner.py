"""Codex CLI runner.

Runs OpenAI Codex through the CLI using a ChatGPT/Codex auth.json mounted by
the orchestrator into /shared/.codex/auth.json.
"""

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
    TASK_STARTUP_PREFIX,
    get_improvement_context,
    get_marketplace_skill_suggestions,
    get_memory_preload,
    get_skill_preload,
    get_skills_context,
    get_user_feedback,
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


def _extract_tool_call(event: dict) -> tuple[str, dict] | None:
    item = event.get("item") if isinstance(event.get("item"), dict) else event
    typ = str(item.get("type", event.get("type", ""))).lower()
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
    return str(name), args if isinstance(args, dict) else {"raw": args}


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
        if lightweight:
            enhanced_prompt = task_id_line + prompt
        else:
            enhanced_prompt = (
                task_id_line
                + TASK_STARTUP_PREFIX
                + get_memory_preload()
                + get_user_feedback()
                + get_skill_preload()
                + get_skills_context()
                + get_marketplace_skill_suggestions(prompt[:200])
                + get_improvement_context()
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
        cmd = [
            "codex", "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C", settings.workspace_dir,
            "-m", model,
            prompt,
        ]
        env = _codex_env()

        stderr_lines: list[str] = []
        text_output: list[str] = []
        result_data: dict = {"status": "completed", "result": ""}

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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )
            stderr_task = asyncio.create_task(collect_stderr(self._process))

            async for event in _stream_jsonl(self._process):
                text = _extract_text(event)
                if text:
                    text_output.append(text)
                    await _publish(self.log_publisher, stream, target_id, "text", {"text": text})

                tool_call = _extract_tool_call(event)
                if tool_call:
                    name, args = tool_call
                    await _publish(
                        self.log_publisher,
                        stream,
                        target_id,
                        "tool_call",
                        {"tool_use_id": event.get("id", ""), "tool": name, "input": args},
                    )

                if str(event.get("type", "")).endswith("completed"):
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
                error = "\n".join(stderr_lines).strip() or f"Codex CLI exited with code {returncode}"
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
    env.setdefault("CODEX_HOME", "/home/agent/.codex")
    return env


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
