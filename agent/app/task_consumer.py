import asyncio
import json

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher


class TaskConsumer:
    """Consumes tasks from a Redis queue and executes them via AgentRunner or LLMRunner."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:tasks"
        self.running = True
        self._runner = None  # AgentRunner or LLMRunner

    async def start(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        log_publisher = LogPublisher(self.redis, self.agent_id)

        # Choose runner based on agent mode
        if settings.agent_mode == "custom_llm":
            from app.llm_runner import LLMRunner
            self._runner = LLMRunner(log_publisher)
        else:
            from app.agent_runner import AgentRunner
            self._runner = AgentRunner(log_publisher)

        # Report as ready
        await log_publisher.publish_status("idle")
        await log_publisher.publish("", "system", {"message": f"Agent {self.agent_id} ready"})

        while self.running:
            task_id = None
            try:
                # BRPOP blocks until a task is available (timeout 5s for health checks)
                result = await self.redis.brpop(self.queue_name, timeout=5)
                if result is None:
                    # Timeout - just loop back (allows health checks and graceful shutdown)
                    continue

                _, task_json = result
                task = json.loads(task_json)
                task_id = task["id"]

                # Update status to working
                await log_publisher.publish_status("working", task_id)

                # Notify orchestrator that task is now running
                await self.redis.publish(
                    "task:started",
                    json.dumps({"task_id": task_id, "agent_id": self.agent_id}),
                )

                model = task.get("model") or "default"
                prompt_preview = task["prompt"][:80] + ("..." if len(task["prompt"]) > 80 else "")
                await log_publisher.publish(task_id, "system", {
                    "message": f"Task started: {prompt_preview} (model: {model})"
                })

                # Execute the task
                result_data = await self._runner.execute_task(
                    task_id=task_id,
                    prompt=task["prompt"],
                    model=task.get("model"),
                )

                # Log completion
                status = result_data.get("status", "unknown")
                cost = result_data.get("cost_usd", 0)
                duration = result_data.get("duration_ms", 0)
                turns = result_data.get("num_turns", 0)
                if status == "completed":
                    await log_publisher.publish(task_id, "system", {
                        "message": f"Task completed (${cost:.4f}, {duration}ms, {turns} turns)"
                    })
                else:
                    error = result_data.get("error", "Unknown error")[:100]
                    await log_publisher.publish(task_id, "system", {
                        "message": f"Task failed: {error}"
                    })

                # Report completion back via Redis pub/sub
                await self.redis.publish(
                    "task:completions",
                    json.dumps(
                        {
                            "task_id": task_id,
                            "agent_id": self.agent_id,
                            **result_data,
                        }
                    ),
                )

                # Update status back to idle
                await log_publisher.publish_status("idle")

            except aioredis.ConnectionError:
                # Redis connection lost, wait and retry
                await asyncio.sleep(2)
            except Exception as e:
                # Report failure back to orchestrator so the task doesn't stay stuck
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
                        await log_publisher.publish(
                            task_id, "error", {"message": error_msg}
                        )
                        await log_publisher.publish_status("idle")
                except Exception:
                    pass  # Best effort - don't let reporting crash the loop
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self.running = False
        if self._runner and self._runner.is_running:
            await self._runner.interrupt()
        if self.redis:
            await self.redis.aclose()
