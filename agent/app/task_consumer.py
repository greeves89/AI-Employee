import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher

logger = logging.getLogger(__name__)


def _max_parallel_tasks() -> int:
    """How many tasks ONE agent runs at the same time. Default 1 = serial (unchanged
    behaviour). Set MAX_PARALLEL_TASKS>1 to let independent tasks run concurrently —
    each in its own runner subprocess. Mirrors MAX_PARALLEL_CHATS on the chat side."""
    try:
        return max(1, int(os.getenv("MAX_PARALLEL_TASKS", "1")))
    except (TypeError, ValueError):
        return 1


class TaskConsumer:
    """Consumes tasks from a Redis queue and executes them via AgentRunner or LLMRunner.

    Parallel mode (MAX_PARALLEL_TASKS>1): the main loop only pulls a task from Redis
    when a semaphore slot is free, then dispatches it to its OWN runner instance so up
    to N independent tasks run concurrently without sharing a subprocess. In serial
    mode (default) the semaphore has one slot → behaviour is identical to before.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:tasks"
        self.running = True
        self._log_publisher: LogPublisher | None = None
        self._sem: asyncio.Semaphore | None = None
        self._inflight: set[asyncio.Task] = set()      # dispatched task coroutines
        self._active_runners: set = set()              # runners currently executing (for stop())

    def _make_runner(self):
        """Fresh runner instance per task — independent subprocess, no shared state."""
        if settings.agent_mode == "custom_llm":
            from app.llm_runner import LLMRunner
            return LLMRunner(self._log_publisher)
        elif settings.agent_mode == "codex_cli":
            from app.codex_runner import CodexAgentRunner
            return CodexAgentRunner(self._log_publisher)
        else:
            from app.agent_runner import AgentRunner
            return AgentRunner(self._log_publisher)

    async def start(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        self._log_publisher = LogPublisher(self.redis, self.agent_id)
        max_parallel = _max_parallel_tasks()
        self._sem = asyncio.Semaphore(max_parallel)

        # Report as ready
        await self._log_publisher.publish_status("idle")
        await self._log_publisher.publish("", "system", {"message": f"Agent {self.agent_id} ready"})
        if max_parallel > 1:
            logger.info("Task parallelism enabled: up to %d tasks concurrently", max_parallel)

        while self.running:
            # Only pull a new task once a slot is free → at most N tasks in flight,
            # the rest stay durably in Redis.
            await self._sem.acquire()
            if not self.running:
                self._sem.release()
                break
            try:
                # BRPOP blocks until a task is available (timeout 5s for health checks)
                result = await self.redis.brpop(self.queue_name, timeout=5)
                if result is None:
                    # Timeout - release the slot and loop (health checks, shutdown)
                    self._sem.release()
                    continue

                _, task_json = result
                task = json.loads(task_json)
                # Dispatch concurrently; the coroutine releases the slot when done.
                t = asyncio.create_task(self._run_task(task))
                self._inflight.add(t)
                t.add_done_callback(self._inflight.discard)
            except aioredis.TimeoutError:
                self._sem.release()
                continue
            except aioredis.ConnectionError:
                self._sem.release()
                await asyncio.sleep(2)
            except Exception as e:  # noqa: BLE001 — never let the dispatch loop die
                self._sem.release()
                logger.warning("Task dispatch error: %s", e)
                await asyncio.sleep(1)

    async def _run_task(self, task: dict) -> None:
        """Execute ONE task in its own runner, then release the semaphore slot."""
        task_id = task.get("id")
        runner = self._make_runner()
        self._active_runners.add(runner)
        # Any task working → agent shows "working"; when the last finishes we go idle.
        try:
            await self._log_publisher.publish_status("working", task_id)
            await self.redis.publish(
                "task:started",
                json.dumps({"task_id": task_id, "agent_id": self.agent_id}),
            )
            model = task.get("model") or "default"
            prompt_preview = task["prompt"][:80] + ("..." if len(task["prompt"]) > 80 else "")
            await self._log_publisher.publish(task_id, "system", {
                "message": f"Task started: {prompt_preview} (model: {model})"
            })

            is_lightweight = task.get("lightweight", False)
            result_data = await runner.execute_task(
                task_id=task_id,
                prompt=task["prompt"],
                model=task.get("model"),
                lightweight=is_lightweight,
            )

            status = result_data.get("status", "unknown")
            cost = result_data.get("cost_usd", 0)
            duration = result_data.get("duration_ms", 0)
            turns = result_data.get("num_turns", 0)
            if status == "completed":
                await self._log_publisher.publish(task_id, "system", {
                    "message": f"Task completed (${cost:.4f}, {duration}ms, {turns} turns)"
                })
            else:
                error = result_data.get("error", "Unknown error")[:100]
                await self._log_publisher.publish(task_id, "system", {
                    "message": f"Task failed: {error}"
                })

            await self.redis.publish(
                "task:completions",
                json.dumps({"task_id": task_id, "agent_id": self.agent_id, **result_data}),
            )
        except Exception as e:  # noqa: BLE001
            error_msg = f"Consumer error: {e}"
            try:
                if self.redis and task_id:
                    await self.redis.publish(
                        "task:completions",
                        json.dumps({
                            "task_id": task_id,
                            "agent_id": self.agent_id,
                            "status": "failed",
                            "error": error_msg[:500],
                        }),
                    )
                    await self._log_publisher.publish(task_id, "error", {"message": error_msg})
            except Exception:
                pass  # best effort
        finally:
            self._active_runners.discard(runner)
            # Only flip to idle when no other task is still running.
            if not self._active_runners:
                try:
                    await self._log_publisher.publish_status("idle")
                except Exception:
                    pass
            self._sem.release()

    async def stop(self) -> None:
        self.running = False
        for runner in list(self._active_runners):
            try:
                if getattr(runner, "is_running", False):
                    await runner.interrupt()
            except Exception:
                pass
        if self.redis:
            await self.redis.aclose()
