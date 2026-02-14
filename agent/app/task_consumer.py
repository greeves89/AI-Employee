import asyncio
import json

import redis.asyncio as aioredis

from app.agent_runner import AgentRunner
from app.config import settings
from app.log_publisher import LogPublisher


class TaskConsumer:
    """Consumes tasks from a Redis queue and executes them via AgentRunner."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:tasks"
        self.running = True
        self._runner: AgentRunner | None = None

    async def start(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        log_publisher = LogPublisher(self.redis, self.agent_id)
        self._runner = AgentRunner(log_publisher)

        # Report as ready
        await log_publisher.publish_status("idle")
        await log_publisher.publish("", "system", {"message": f"Agent {self.agent_id} ready"})

        while self.running:
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

                # Execute the task
                result_data = await self._runner.execute_task(
                    task_id=task_id,
                    prompt=task["prompt"],
                    model=task.get("model"),
                )

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
                # Log unexpected errors but keep running
                if self.redis:
                    await log_publisher.publish(
                        "", "error", {"message": f"Consumer error: {e}"}
                    )
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self.running = False
        if self._runner and self._runner.is_running:
            await self._runner.interrupt()
        if self.redis:
            await self.redis.aclose()
