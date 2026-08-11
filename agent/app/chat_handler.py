"""Chat handler - manages interactive conversation sessions with Claude CLI."""

import asyncio
import json
import time
import logging
import os
import signal
from typing import AsyncIterator

from app.config import get_oauth_token, settings
from app.log_publisher import LogPublisher
from app.runner_hooks import feed_prompt_via_stdin

logger = logging.getLogger(__name__)


class ChatHandler:
    """Handles interactive chat sessions using Claude Code CLI with --resume."""

    def __init__(self, log_publisher: LogPublisher):
        self.log_publisher = log_publisher
        self.session_id: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        self.is_running = False
        # Live steering: set by the ChatConsumer to an async callable returning the
        # list of messages that arrived on this channel mid-turn (see steering.py).
        self.pending_drain = None

    async def _run_turn_with_retries(
        self, message_id: str, text: str, model: str
    ) -> dict:
        """One Claude CLI turn (resumes via self.session_id) + session/auth retries."""
        # Vor dem Lauf merken, WELCHER Token benutzt wurde — nur so laesst sich
        # spaeter feststellen, ob die Plattform inzwischen einen neuen hinterlegt
        # hat, statt blind eine Weile zu warten.
        from app.config import get_oauth_token
        token_before = get_oauth_token()
        result = await self._execute_cli(message_id, text, model)

        # If --resume failed, reset session and retry without it
        if (
            result.get("status") == "error"
            and self.session_id
            and "no conversation found" in result.get("error", "").lower()
        ):
            logger.warning(f"Session {self.session_id} not found, resetting and retrying")
            self.session_id = None
            await self.log_publisher.publish_chat(
                message_id, "system",
                {"message": "Session expired, starting fresh conversation..."},
            )
            result = await self._execute_cli(message_id, text, model)

        # Zugangsfehler: auf den ERNEUERTEN Token warten und wiederholen.
        #
        # Anthropic rotiert beim Erneuern — in der Sekunde, in der der neue Token
        # ausgestellt wird, ist der alte tot. Faellt das in einen laufenden Zug,
        # stirbt er mit „401 access token has been revoked", ohne dass jemand
        # etwas falsch gemacht hat. Genau so ist es im Betrieb passiert.
        #
        # Frueher standen hier pauschal 10 Sekunden. Die Plattform schreibt den
        # neuen Token aber erst im naechsten 30-Sekunden-Takt — der Versuch lief
        # ins Leere, und der Nutzer bekam den Fehler rot in den Chat. Jetzt wird
        # gewartet, bis sich der Token wirklich geaendert hat.
        error_text = result.get("error", "").lower()
        if result.get("status") == "error" and any(
            phrase in error_text
            for phrase in [
                "does not have access", "invalid_grant", "unauthorized",
                "401", "token", "oauth", "authentication", "revoked",
            ]
        ):
            logger.warning(f"Auth error detected, waiting for token refresh: {error_text[:100]}")
            await self.log_publisher.publish_chat(
                message_id, "system",
                {"message": "Zugang wird erneuert — der Schritt wird gleich wiederholt."},
            )
            from app.config import wait_for_new_oauth_token
            await wait_for_new_oauth_token(token_before)
            result = await self._execute_cli(message_id, text, model)
        return result

    # Claude has no reasoning-effort flag; thinking depth is driven by the
    # MAX_THINKING_TOKENS budget. Mapped from the user's per-message choice.
    _THINKING_BUDGET = {"low": "4000", "medium": "10000", "high": "31999"}

    async def handle_message(
        self, message_id: str, text: str, model: str | None = None,
        reasoning: str = "",
    ) -> dict:
        """Send a chat message to Claude CLI and stream the response.

        Live steering: if a new message arrives on this channel while the turn runs,
        it interrupts (SIGINT → -2, graceful) and folds the message into a follow-up
        turn that resumes the same session (--resume). See steering.py.
        """
        from app.steering import run_turns_with_steering
        model = model or settings.default_model
        # Held on the instance for this turn (incl. steering follow-ups) instead of
        # threaded through _run_turn_with_retries' three call sites.
        self._reasoning = reasoning or ""
        self.is_running = True

        async def _run_turn(t: str, _is_resume: bool) -> dict:
            # session_id (set on the first turn) makes _execute_cli use --resume,
            # so a folded message continues the SAME conversation.
            return await self._run_turn_with_retries(message_id, t, model)

        try:
            result = await run_turns_with_steering(
                initial_text=text,
                run_turn=_run_turn,
                stop_current=self.stop_current,
                pending_drain=self.pending_drain,
                publish_system=lambda m: self.log_publisher.publish_chat(
                    message_id, "system", {"message": m}
                ),
            )
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

        # Prompt via stdin, not argv — avoids "Argument list too long" on large input.
        cmd.extend([
            "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            # AskUserQuestion is a CLI-builtin that expects an interactive
            # terminal. Headless (-p) there is nobody to answer, so it returns
            # the stub "Answer questions?" — the agent treats that as a reply
            # and carries on with its own guesses, while the user only sees raw
            # JSON in the chat and cannot answer at all. Taking it away makes
            # the agent ask in plain text, which the chat (incl. live steering)
            # already handles properly.
            "--disallowedTools", "AskUserQuestion",
            "--model", model,
        ])

        env = os.environ.copy()
        reasoning = getattr(self, "_reasoning", "")
        if reasoning == "off":
            env.pop("MAX_THINKING_TOKENS", None)
        elif reasoning in self._THINKING_BUDGET:
            env["MAX_THINKING_TOKENS"] = self._THINKING_BUDGET[reasoning]
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        else:
            oauth_token = get_oauth_token()
            if oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

        self._interrupted = False  # set by stop_current() when steering cuts this turn short
        result_data: dict = {"status": "completed", "text": ""}
        stream_had_error = False
        accumulated_tool_calls: list[dict] = []  # Track tool calls for persistence
        stderr_lines: list[str] = []  # Collect stderr concurrently
        seen_file_payloads: set[str] = set()

        async def _publish_present_file_if_marker(marker_text: str) -> bool:
            if not marker_text.startswith("__AI_EMPLOYEE_PRESENT_FILE__"):
                return False
            payload_raw = marker_text.removeprefix("__AI_EMPLOYEE_PRESENT_FILE__")
            if payload_raw in seen_file_payloads:
                return True
            try:
                payload = json.loads(payload_raw)
                seen_file_payloads.add(payload_raw)
                await self.log_publisher.publish_chat(message_id, "file", payload)
            except Exception:
                logger.debug("Could not parse present_file payload", exc_info=True)
            return True

        def _first_text_from_tool_result_content(content) -> str:
            if isinstance(content, str):
                return content
            if isinstance(content, dict):
                if isinstance(content.get("text"), str):
                    return content["text"]
                if "content" in content:
                    return _first_text_from_tool_result_content(content["content"])
            if isinstance(content, list):
                for block in content:
                    text = _first_text_from_tool_result_content(block)
                    if text:
                        return text
            return ""

        async def _collect_stderr(proc: asyncio.subprocess.Process) -> None:
            """Read stderr concurrently so it's not lost when process exits."""
            if not proc.stderr:
                return
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                self.log_publisher.last_activity_at = time.monotonic()   # auch das ist Leben
                decoded = line.decode("utf-8", errors="replace").strip()
                if decoded:
                    stderr_lines.append(decoded)
                    logger.warning(f"[Claude CLI stderr] {decoded}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )

            # Feed the prompt via stdin; read stderr in background so we capture it
            # even if the CLI crashes.
            stdin_task = asyncio.create_task(
                feed_prompt_via_stdin(self._process, text)
            )
            stderr_task = asyncio.create_task(_collect_stderr(self._process))

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
                    content = event.get("content", "")
                    marker_text = _first_text_from_tool_result_content(content)
                    is_present_file = await _publish_present_file_if_marker(marker_text)
                    await self.log_publisher.publish_chat(
                        message_id,
                        "tool_result",
                        {
                            "tool_use_id": event.get("tool_use_id", ""),
                            "content": "File presented to the user."
                            if is_present_file else content,
                        },
                    )

                elif event_type == "user":
                    # Claude Code stream-json may emit MCP tool results as a
                    # synthetic user message with content blocks of type
                    # "tool_result" instead of a top-level tool_result event.
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        content = block.get("content", "")
                        marker_text = _first_text_from_tool_result_content(content)
                        is_present_file = await _publish_present_file_if_marker(marker_text)
                        await self.log_publisher.publish_chat(
                            message_id,
                            "tool_result",
                            {
                                "tool_use_id": block.get("tool_use_id", ""),
                                "content": "File presented to the user."
                                if is_present_file else content,
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
                        # Internal CLI diagnostics (e.g. ede_diagnostic) are not user errors
                        if error_msg.startswith("[ede_diagnostic]") or error_msg.startswith("[diagnostic]"):
                            logger.debug(f"Suppressed CLI diagnostic: {error_msg}")
                        else:
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
            # Ensure stderr collection and stdin feed are complete
            await stderr_task
            await stdin_task

            if returncode != 0 and not stream_had_error:
                # Code -2 (SIGINT) = graceful interrupt for queued messages — not an error.
                # Also honor the explicit _interrupted flag (some exit codes vary).
                if returncode == -2 or getattr(self, "_interrupted", False):
                    logger.info("Claude CLI interrupted — folding new message(s)")
                else:
                    stderr_text = "\n".join(stderr_lines).strip()
                    error_msg = stderr_text or f"Claude CLI exited with code {returncode}"
                    logger.error(f"Claude CLI failed (code {returncode}): {error_msg}")
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
            # Lebenszeichen auf der UNTERSTEN Ebene: jede Regung der CLI zaehlt, nicht
            # erst ein veroeffentlichtes Chat-Ereignis. Laeuft die CLI minutenlang in
            # einem einzigen langen Werkzeug (Build, Installation), kommt oben nichts
            # an — der Stillstands-Wachhund hielt den Agenten dann faelschlich fuer
            # haengend und brach nach 600s ab, mitten in echter Arbeit.
            if chunk:
                self.log_publisher.last_activity_at = time.monotonic()
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

    async def stop_current(self) -> None:
        """Stop the currently running Claude CLI process."""
        self._interrupted = True
        if self._process and self._process.returncode is None:
            logger.info("Stopping current chat process (SIGINT)")
            try:
                self._process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("SIGINT timeout, killing process")
                self._process.kill()
            except ProcessLookupError:
                pass  # Process already exited

    async def reset_session(self) -> None:
        """Reset the chat session (start a new conversation)."""
        self.session_id = None
        await self.log_publisher.publish_chat(
            "", "system", {"message": "Chat session reset"}
        )
