import asyncio
import json
import os
import signal
from typing import AsyncIterator

from app.config import get_oauth_token, settings
from app.log_publisher import LogPublisher


SELF_IMPROVEMENT_SUFFIX = """

---
IMPORTANT FINAL STEPS (do ALL of these before finishing):

1. **VALIDATE before committing**: If you wrote code, run the build/test command
   (e.g. `npm run build`, `pytest`, `go build`) to verify everything compiles.
   Fix any errors before committing. NEVER commit broken code.

2. **Push your work**: After committing, always `git push`. Never leave work only local.

3. **Save learnings via MCP**: If you learned something useful during this task,
   use the `memory_save` MCP tool (category: "learning") to remember it.
   Examples: patterns that worked, errors and fixes, useful commands, project conventions.
   Do NOT write to CLAUDE.md or MEMORY.md — use `memory_save` so the user can see it.

4. **Review & update knowledge.md**: Read `/workspace/knowledge.md` and update it:
   - Add new learned patterns to "## Learned Patterns" (e.g. library quirks, architecture decisions)
   - Add errors you encountered and how you fixed them to "## Errors & Fixes"
   - If your role or responsibilities changed during this task, update those sections too
   - Keep it concise — knowledge.md is your persistent profile that you read at task start
"""


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
        self, task_id: str, prompt: str, model: str | None = None
    ) -> dict:
        """Execute a task using Claude Code CLI in headless mode."""
        model = model or settings.default_model
        self.is_running = True

        # Enhance prompt with self-improvement instruction
        enhanced_prompt = prompt + SELF_IMPROVEMENT_SUFFIX

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

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=settings.workspace_dir,
                env=env,
            )

            async for event in self._stream_output(self._process):
                await self._process_event(task_id, event)

                if event.get("type") == "result":
                    result_data = {
                        "status": "completed",
                        "duration_ms": event.get("duration_ms"),
                        "num_turns": event.get("num_turns"),
                        "cost_usd": event.get("cost_usd", 0),
                        "result": event.get("result", ""),
                    }

            returncode = await self._process.wait()
            if returncode != 0:
                stderr = ""
                if self._process.stderr:
                    stderr_bytes = await self._process.stderr.read()
                    stderr = stderr_bytes.decode("utf-8", errors="replace")
                result_data = {
                    "status": "error",
                    "error": f"Claude CLI exited with code {returncode}: {stderr}",
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
            await self.log_publisher.publish(
                task_id,
                "result",
                {
                    "cost_usd": event.get("cost_usd", 0),
                    "duration_ms": event.get("duration_ms", 0),
                    "num_turns": event.get("num_turns", 0),
                },
            )

        elif event_type == "raw":
            await self.log_publisher.publish(
                task_id, "raw", {"text": event.get("text", "")}
            )

    async def interrupt(self) -> None:
        """Interrupt the currently running task."""
        if self._process and self._process.returncode is None:
            try:
                self._process.send_signal(signal.SIGINT)
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
