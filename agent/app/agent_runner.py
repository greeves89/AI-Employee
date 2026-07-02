import asyncio
import json
import logging
import os
import signal
from typing import AsyncIterator

from app.config import get_oauth_token, settings
from app.log_publisher import LogPublisher
from app.runner_hooks import (
    SELF_IMPROVEMENT_SUFFIX,
    compose_prompt_bundle,
)

logger = logging.getLogger(__name__)


class AgentRunner:
    """Wraps Claude Code CLI in headless mode (-p + --output-format stream-json).

    Executes tasks by spawning the CLI as a subprocess and streaming
    NDJSON output back via the log publisher.
    """

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self._process: asyncio.subprocess.Process | None = None
        self.is_running = False

    async def execute_task(
        self, task_id: str, prompt: str, model: str | None = None,
        lightweight: bool = False,
    ) -> dict:
        """Execute a task using Claude Code CLI in headless mode.

        Args:
            lightweight: If True, skip startup checks and self-improvement
                        reflection. Use for chat/telegram messages where
                        speed matters more than knowledge preloading.
        """
        model = model or settings.default_model
        self.is_running = True

        task_id_line = f"CURRENT_TASK_ID: {task_id}\n\n"

        # Unified context bundle (shared by all runtimes via runner_hooks): startup
        # prefix + memory + skills + host mounts/Second Brain + marketplace + (full:
        # user feedback + improvement). Mode-specific delivery stays here.
        enhanced_prompt = (
            task_id_line
            + compose_prompt_bundle(prompt, lightweight)
            + prompt
            + SELF_IMPROVEMENT_SUFFIX
        )

        cmd = [
            "claude",
            "-p", enhanced_prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--max-turns", str(settings.max_turns),
            "--model", model,
        ]

        env = os.environ.copy()
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        else:
            oauth_token = get_oauth_token()
            if oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        await self.log_publisher.publish(
            task_id, "system", {"message": f"Starting task with model {model}"}
        )

        result_data: dict = {"status": "completed"}
        got_result = False
        stderr_lines: list[str] = []
        text_output: list[str] = []
        presented_files: list[dict] = []
        seen_file_paths: set[str] = set()

        async def _collect_stderr(proc: asyncio.subprocess.Process) -> None:
            """Read stderr concurrently so it's not lost when process exits."""
            if not proc.stderr:
                return
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    logger.warning(f"[Claude CLI stderr] {decoded}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )

            # Read stderr in background
            stderr_task = asyncio.create_task(_collect_stderr(self._process))

            async for event in self._stream_output(self._process):
                await self._process_event(task_id, event)

                for payload in self._present_file_payloads_from_event(event):
                    path = str(payload.get("path") or "")
                    if not path or path in seen_file_paths:
                        continue
                    seen_file_paths.add(path)
                    presented_files.append(payload)
                    await self._mirror_present_file_to_chat(task_id, payload)
                    await self._deliver_present_file_via_telegram(payload)

                # Collect assistant text output
                if event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text" and block.get("text"):
                            text_output.append(block["text"])

                if event.get("type") == "result":
                    got_result = True
                    result_text = event.get("result", "") or "\n".join(text_output)
                    usage = event.get("usage", {}) or {}
                    result_data = {
                        "status": "completed",
                        "duration_ms": event.get("duration_ms"),
                        "num_turns": event.get("num_turns"),
                        # CLI emits "total_cost_usd"; keep "cost_usd" as legacy fallback
                        "cost_usd": event.get("total_cost_usd", event.get("cost_usd", 0)) or 0,
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "result": result_text,
                    }

            returncode = await self._process.wait()
            await stderr_task

            # A successful run emits a "result" event before exiting. If we already
            # captured that result, a non-zero exit code (CLI cleanup/stdin quirks)
            # must NOT turn a completed task into a false failure — only report an
            # error when no result was produced.
            if returncode != 0 and not got_result:
                stderr_text = "\n".join(stderr_lines).strip()
                error_msg = stderr_text or f"Claude CLI exited with code {returncode}"
                logger.error(f"Claude CLI failed (code {returncode}): {error_msg}")
                result_data = {
                    "status": "error",
                    "error": f"Claude CLI exited with code {returncode}: {error_msg}",
                }
                await self.log_publisher.publish(
                    task_id, "error", {"message": result_data["error"]}
                )

        except asyncio.CancelledError:
            await self.interrupt()
            result_data = {"status": "cancelled"}
        except Exception as e:
            result_data = {"status": "error", "error": str(e)}
            await self.log_publisher.publish(
                task_id, "error", {"message": str(e)}
            )
        finally:
            self.is_running = False
            self._process = None

        if presented_files:
            result_data.setdefault("presented_files", []).extend(presented_files)
            await self._publish_scheduler_chat_completion(
                task_id=task_id,
                presented_files=presented_files,
                final_text=str(result_data.get("result") or "\n".join(text_output)).strip(),
                result_data=result_data,
            )

        return result_data

    async def _stream_output(
        self, process: asyncio.subprocess.Process
    ) -> AsyncIterator[dict]:
        """Stream NDJSON lines from the subprocess stdout."""
        if not process.stdout:
            return

        buffer = b""
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                # Process ended, parse remaining buffer
                if buffer.strip():
                    for line in buffer.decode("utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if line:
                            try:
                                yield json.loads(line)
                            except json.JSONDecodeError:
                                pass
                break

            buffer += chunk
            while b"\n" in buffer:
                line_bytes, buffer = buffer.split(b"\n", 1)
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        # Non-JSON output (e.g. progress indicators)
                        yield {"type": "raw", "text": line}

    async def _process_event(self, task_id: str, event: dict) -> None:
        """Process a single NDJSON event and publish it."""
        event_type = event.get("type", "unknown")

        if event_type == "assistant":
            message = event.get("message", {})
            content_blocks = message.get("content", [])
            for block in content_blocks:
                if block.get("type") == "text":
                    await self.log_publisher.publish(
                        task_id, "text", {"text": block["text"]}
                    )
                elif block.get("type") == "tool_use":
                    await self.log_publisher.publish(
                        task_id,
                        "tool_call",
                        {
                            "tool_use_id": block.get("id", ""),
                            "tool": block.get("name", "unknown"),
                            "input": block.get("input", {}),
                        },
                    )

        elif event_type == "tool_result":
            await self.log_publisher.publish(
                task_id,
                "tool_result",
                {
                    "tool_use_id": event.get("tool_use_id", ""),
                    "content": event.get("content", ""),
                },
            )

        elif event_type == "result":
            _usage = event.get("usage", {}) or {}
            await self.log_publisher.publish(
                task_id,
                "result",
                {
                    "cost_usd": event.get("total_cost_usd", event.get("cost_usd", 0)) or 0,
                    "input_tokens": _usage.get("input_tokens"),
                    "output_tokens": _usage.get("output_tokens"),
                    "duration_ms": event.get("duration_ms", 0),
                    "num_turns": event.get("num_turns", 0),
                },
            )

        elif event_type == "raw":
            await self.log_publisher.publish(
                task_id, "raw", {"text": event.get("text", "")}
            )

    @staticmethod
    def _first_text_blocks(content) -> list[str]:
        """Return all text-ish values from Claude tool_result content blocks."""
        found: list[str] = []
        if isinstance(content, str):
            found.append(content)
        elif isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                found.append(text)
            if "content" in content:
                found.extend(AgentRunner._first_text_blocks(content["content"]))
        elif isinstance(content, list):
            for block in content:
                found.extend(AgentRunner._first_text_blocks(block))
        return found

    @staticmethod
    def _parse_present_file_marker(content) -> dict | None:
        """Extract a present_file payload from MCP marker content."""
        marker = "__AI_EMPLOYEE_PRESENT_FILE__"
        for text in AgentRunner._first_text_blocks(content):
            stripped = text.strip()
            if not stripped.startswith(marker):
                continue
            try:
                payload = json.loads(stripped.removeprefix(marker))
            except (json.JSONDecodeError, TypeError):
                return None
            return payload if isinstance(payload, dict) else None
        return None

    def _present_file_payloads_from_event(self, event: dict) -> list[dict]:
        """Find present_file marker payloads in Claude task-stream events.

        Claude Code can emit MCP tool results either as top-level
        `tool_result` events or as synthetic `user` messages containing
        `tool_result` blocks. The chat path already handles both; task/scheduler
        execution needs the same coverage so files land in chat history.
        """
        event_type = event.get("type")
        payloads: list[dict] = []

        if event_type == "tool_result":
            payload = self._parse_present_file_marker(event.get("content"))
            if payload:
                payloads.append(payload)

        if event_type == "user":
            message = event.get("message", {})
            for block in message.get("content", []) or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                payload = self._parse_present_file_marker(block.get("content"))
                if payload:
                    payloads.append(payload)

        return payloads

    async def _mirror_present_file_to_chat(self, task_id: str, payload: dict) -> None:
        """Publish a live chat file event for scheduler/task present_file output."""
        try:
            await self.log_publisher.publish_chat(task_id, "file", payload)
        except Exception:
            logger.warning("Failed to mirror present_file to chat channel", exc_info=True)

    async def _deliver_present_file_via_telegram(self, payload: dict) -> None:
        """Reliably push a scheduler/task present_file to the user via Telegram.

        The chat mirror in _mirror_present_file_to_chat only reaches WebSocket
        clients that happen to be connected. Scheduled runs (e.g. the 06:00
        podcast) fire when no client is attached, so the file silently never
        arrives (issue #208). Telegram's Bot API is a durable push channel, so
        we also base64-encode the file onto agent:{id}:telegram:send, which the
        agent bot delivers as a document. It is a no-op for agents that have no
        authorized Telegram chats, so inter-agent tasks are unaffected.
        """
        path = str(payload.get("path") or "")
        if not path:
            return
        # SECURITY: only deliver files from THIS agent's own workspace. Never an
        # arbitrary container path — otherwise an agent could exfiltrate mounted
        # brain vaults, /shared, or container secrets out via Telegram, bypassing
        # the autonomy/approval controls. present_file is meant for /workspace output.
        try:
            real = os.path.realpath(path)
            ws = os.path.realpath(settings.workspace_dir)
            if real != ws and not real.startswith(ws + os.sep):
                logger.warning("present_file path outside workspace refused: %s", path)
                return
        except Exception:
            return
        if not os.path.isfile(real):
            return
        path = real
        try:
            import base64

            size = os.path.getsize(path)
            # The agent bot decodes the whole file in memory; match the 20 MB
            # cap used by the send_telegram tool. Larger files stay chat-only.
            if size <= 0 or size > 20 * 1024 * 1024:
                return
            with open(path, "rb") as f:
                raw = f.read()
            tg_payload = {
                "agent_id": self.log_publisher.agent_id,
                "file_b64": base64.b64encode(raw).decode("ascii"),
                "media_type": payload.get("media_type") or "application/octet-stream",
                "filename": payload.get("filename") or os.path.basename(path),
                "caption": payload.get("caption") or "",
            }
            await self.log_publisher.redis.publish(
                f"agent:{self.log_publisher.agent_id}:telegram:send",
                json.dumps(tg_payload),
            )
        except Exception:
            logger.warning("Failed to deliver present_file via Telegram", exc_info=True)

    async def _publish_scheduler_chat_completion(
        self,
        task_id: str,
        presented_files: list[dict],
        final_text: str,
        result_data: dict,
    ) -> None:
        """Persist scheduler/task file output through the chat completion path."""
        try:
            from datetime import datetime, timezone

            payload = {
                "agent_id": self.log_publisher.agent_id,
                "message_id": task_id,
                "type": "done",
                "source": "scheduler",
                "data": {
                    "text": final_text,
                    "presented_files": presented_files,
                    "cost_usd": result_data.get("cost_usd"),
                    "duration_ms": result_data.get("duration_ms"),
                    "num_turns": result_data.get("num_turns"),
                    "input_tokens": result_data.get("input_tokens"),
                    "output_tokens": result_data.get("output_tokens"),
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            await self.log_publisher.redis.publish("chat:completions", json.dumps(payload))
        except Exception:
            logger.warning("Failed to publish scheduler chat completion", exc_info=True)

    async def interrupt(self) -> None:
        """Interrupt the currently running task."""
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
