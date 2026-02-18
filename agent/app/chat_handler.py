"""Chat handler - manages interactive conversation sessions with Claude CLI."""

import asyncio
import json
import logging
import os
from typing import AsyncIterator

from app.config import settings
from app.log_publisher import LogPublisher

logger = logging.getLogger(__name__)


class ChatHandler:
    """Handles interactive chat sessions using Claude Code CLI with --resume."""

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.session_id: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self.is_running = False

    async def handle_message(
        self, message_id: str, text: str, model: str | None = None
    ) -> dict:
        """Send a chat message to Claude CLI and stream the response."""
        model = model or settings.default_model
        self.is_running = True

        try:
            result = await self._execute_cli(message_id, text, model)

            # If --resume failed, reset session and retry without it
            if (
                result.get("status") == "error"
                and self.session_id
                and "no conversation found" in result.get("error", "").lower()
            ):
                logger.warning(
                    f"Session {self.session_id} not found, resetting and retrying"
                )
                self.session_id = None
                await self.log_publisher.publish_chat(
                    message_id,
                    "system",
                    {"message": "Session expired, starting fresh conversation..."},
                )
                result = await self._execute_cli(message_id, text, model)

        finally:
            self.is_running = False
            self._process = None

        # Send completion marker
        await self.log_publisher.publish_chat(message_id, "done", result)
        return result

    async def _execute_cli(
        self, message_id: str, text: str, model: str
    ) -> dict:
        """Execute a single Claude CLI invocation and stream results."""
        cmd = ["claude"]

        # Resume previous session for conversation continuity
        if self.session_id:
            cmd.extend(["--resume", self.session_id])

        cmd.extend([
            "-p", text,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--model", model,
        ])

        env = os.environ.copy()
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        elif settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token

        result_data: dict = {"status": "completed", "text": ""}
        stream_had_error = False
        accumulated_tool_calls: list[dict] = []  # Track tool calls for persistence

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )

            full_text = ""
            seen_text_len = 0  # Track how much text we already sent
            seen_tool_ids: set[str] = set()  # Deduplicate tool_use blocks
            async for event in self._stream_output(self._process):
                event_type = event.get("type", "unknown")

                # Capture session ID for resume (from any event that has it)
                if event.get("session_id") and not self.session_id:
                    self.session_id = event["session_id"]
                    logger.info(f"Captured session_id: {self.session_id}")

                if event_type == "assistant":
                    message = event.get("message", {})
                    # Rebuild full text from all text blocks to detect new content
                    current_full_text = ""
                    for block in message.get("content", []):
                        if block.get("type") == "text":
                            current_full_text += block["text"]
                        elif block.get("type") == "tool_use":
                            tool_id = block.get("id", "")
                            if tool_id and tool_id in seen_tool_ids:
                                continue  # Skip already-published tool calls
                            seen_tool_ids.add(tool_id)
                            tool_name = block.get("name", "unknown")
                            tool_input = block.get("input", {})
                            accumulated_tool_calls.append({
                                "tool": tool_name,
                                "input": json.dumps(tool_input)[:200],
                            })
                            await self.log_publisher.publish_chat(
                                message_id,
                                "tool_call",
                                {
                                    "tool_use_id": tool_id,
                                    "tool": tool_name,
                                    "input": tool_input,
                                },
                            )
                    # Detect new assistant turn: if current text is shorter
                    # than what we've already seen, the content array has reset
                    # (new assistant message after tool use in multi-turn)
                    if len(current_full_text) < seen_text_len:
                        seen_text_len = 0

                    # Only send NEW text (delta since last event)
                    if len(current_full_text) > seen_text_len:
                        new_text = current_full_text[seen_text_len:]
                        full_text += new_text
                        seen_text_len = len(current_full_text)
                        await self.log_publisher.publish_chat(
                            message_id, "text", {"text": new_text}
                        )

                elif event_type == "tool_result":
                    await self.log_publisher.publish_chat(
                        message_id,
                        "tool_result",
                        {
                            "tool_use_id": event.get("tool_use_id", ""),
                            "content": event.get("content", ""),
                        },
                    )

                elif event_type == "result":
                    if event.get("is_error"):
                        errors = event.get("errors", [])
                        error_msg = (
                            "; ".join(errors)
                            if errors
                            else event.get("result", "Unknown error")
                        )
                        result_data = {"status": "error", "error": error_msg}
                        stream_had_error = True
                        await self.log_publisher.publish_chat(
                            message_id, "error", {"message": error_msg}
                        )
                    else:
                        # Use accumulated text, fallback to result field
                        final_text = full_text or event.get("result", "")
                        result_data = {
                            "status": "completed",
                            "text": final_text,
                            "cost_usd": event.get("cost_usd", 0),
                            "duration_ms": event.get("duration_ms", 0),
                            "num_turns": event.get("num_turns", 0),
                            "tool_calls": accumulated_tool_calls or None,
                        }
                        # If we got text from result but didn't stream it yet, send it now
                        if not full_text and final_text:
                            await self.log_publisher.publish_chat(
                                message_id, "text", {"text": final_text}
                            )

            returncode = await self._process.wait()
            if returncode != 0 and not stream_had_error:
                # Only publish error from returncode if stream didn't already report one
                stderr = ""
                if self._process.stderr:
                    stderr_bytes = await self._process.stderr.read()
                    stderr = stderr_bytes.decode("utf-8", errors="replace")
                error_msg = stderr.strip() or "Claude CLI failed"
                result_data = {"status": "error", "error": error_msg}
                await self.log_publisher.publish_chat(
                    message_id, "error", {"message": error_msg}
                )

        except Exception as e:
            result_data = {"status": "error", "error": str(e)}
            await self.log_publisher.publish_chat(
                message_id, "error", {"message": str(e)}
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
                        yield {"type": "raw", "text": line}

    async def reset_session(self) -> None:
        """Reset the chat session (start a new conversation)."""
        self.session_id = None
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Chat session reset"}
        )
