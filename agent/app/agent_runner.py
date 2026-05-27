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
    TASK_STARTUP_PREFIX,
    get_improvement_context,
    get_marketplace_skill_suggestions,
    get_memory_preload,
    get_skill_preload,
    get_skills_context,
    get_user_feedback,
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

        if lightweight:
            from app.runner_hooks import CHAT_STARTUP_PREFIX
            skill_preload = get_skill_preload()
            memory_preload = get_memory_preload()
            skills_ctx = get_skills_context()
            enhanced_prompt = (
                task_id_line
                + CHAT_STARTUP_PREFIX
                + memory_preload
                + skill_preload
                + skills_ctx
                + prompt
                + SELF_IMPROVEMENT_SUFFIX
            )
        else:
            # Full mode: startup context + memory + skills + user feedback + performance + self-improvement
            memory_preload = get_memory_preload()
            skill_preload = get_skill_preload()
            skills_ctx = get_skills_context()
            user_feedback = get_user_feedback()
            improvement_ctx = get_improvement_context()
            marketplace_suggestions = get_marketplace_skill_suggestions(prompt[:200])
            enhanced_prompt = (
                task_id_line
                + TASK_STARTUP_PREFIX
                + memory_preload
                + user_feedback
                + skill_preload
                + skills_ctx
                + marketplace_suggestions
                + improvement_ctx
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
        stderr_lines: list[str] = []
        text_output: list[str] = []
        presented_files: list[dict] = []  # collected from `present_file` markers
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

                # Collect assistant text output
                if event.get("type") == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text" and block.get("text"):
                            text_output.append(block["text"])

                # Capture `present_file` deliveries so we can mirror them to the
                # chat channel (live + persisted) — see _maybe_emit_present_file.
                if event.get("type") == "tool_result":
                    marker_payload = self._parse_present_file_marker(event.get("content"))
                    if marker_payload and marker_payload.get("path") not in seen_file_paths:
                        presented_files.append(marker_payload)
                        seen_file_paths.add(marker_payload.get("path", ""))
                        await self._mirror_present_file_to_chat(task_id, marker_payload)

                if event.get("type") == "result":
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

            if returncode != 0:
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
                final_text="\n".join(text_output).strip(),
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
    def _parse_present_file_marker(content) -> dict | None:
        """Extracts a present_file payload from a tool_result content blob.

        The MCP server returns `__AI_EMPLOYEE_PRESENT_FILE__<json>` either as the
        whole result string or wrapped in `[{"type":"text","text":"..."}]`.
        """
        marker = "__AI_EMPLOYEE_PRESENT_FILE__"

        def _extract(text: str) -> dict | None:
            if not text or not text.startswith(marker):
                return None
            try:
                return json.loads(text.removeprefix(marker))
            except (json.JSONDecodeError, TypeError):
                return None

        if isinstance(content, str):
            return _extract(content)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    payload = _extract(str(block.get("text", "")))
                    if payload:
                        return payload
        return None

    async def _mirror_present_file_to_chat(self, task_id: str, payload: dict) -> None:
        """Push a `file` event onto the agent chat channel so iOS/Web see the
        attachment live, regardless of whether the task ran from the chat or
        scheduler queue.
        """
        try:
            await self.log_publisher.publish_chat(task_id, "file", payload)
        except Exception:
            logger.warning("Failed to mirror present_file to chat channel", exc_info=True)

    async def _publish_scheduler_chat_completion(
        self,
        task_id: str,
        presented_files: list[dict],
        final_text: str,
        result_data: dict,
    ) -> None:
        """Synthesise a chat:completions event for scheduler-originated tasks so
        the orchestrator persists a chat_message row with the file attachments.

        Distinguished by `source="scheduler"`. The orchestrator's
        _listen_chat_completions handler creates a `session_id="scheduler"` row
        when no matching user-message exists, avoiding interference with the
        normal chat flow.
        """
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
