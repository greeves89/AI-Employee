import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.models.task import Task, TaskStatus
from app.services.redis_service import RedisService


class TaskRouter:
    """Routes tasks to agents via Redis queues with load balancing."""

    def __init__(self, db: AsyncSession, redis: RedisService, load_balancer: LoadBalancer):
        self.db = db
        self.redis = redis
        self.load_balancer = load_balancer

    async def create_and_route_task(
        self,
        title: str,
        prompt: str,
        priority: int = 1,
        agent_id: str | None = None,
        model: str | None = None,
    ) -> Task:
        task_id = uuid.uuid4().hex[:12]

        # Auto-assign if no agent specified
        if not agent_id:
            agent_id = await self.load_balancer.select_agent(priority=priority)
            if not agent_id:
                # No agents available - create task as pending
                task = Task(
                    id=task_id,
                    title=title,
                    prompt=prompt,
                    status=TaskStatus.PENDING,
                    priority=priority,
                    model=model,
                )
                self.db.add(task)
                await self.db.commit()
                await self.db.refresh(task)
                return task

        # Create task in DB
        task = Task(
            id=task_id,
            title=title,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            priority=priority,
            agent_id=agent_id,
            model=model,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(task)
        await self.db.commit()

        # Push to agent's Redis queue
        task_payload = json.dumps(
            {
                "id": task_id,
                "prompt": prompt,
                "model": model,
                "priority": priority,
            }
        )
        await self.redis.push_task(agent_id, task_payload)

        await self.db.refresh(task)
        return task

    async def handle_task_completion(self, data: dict) -> None:
        task_id = data["task_id"]
        agent_id = data.get("agent_id")

        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return

        task.status = TaskStatus.COMPLETED if data.get("status") == "completed" else TaskStatus.FAILED
        task.result = data.get("result")
        task.error = data.get("error")
        task.cost_usd = data.get("cost_usd")
        task.duration_ms = data.get("duration_ms")
        task.num_turns = data.get("num_turns")
        task.completed_at = datetime.now(timezone.utc)

        # Update agent metrics for self-improvement tracking
        if agent_id:
            await self._update_agent_metrics(agent_id, data)

        # Update schedule stats if this task belongs to a schedule
        schedule_id = (task.metadata_ or {}).get("schedule_id")
        if schedule_id:
            await self._update_schedule_stats(schedule_id, data)

        await self.db.commit()

    async def _update_agent_metrics(self, agent_id: str, result_data: dict) -> None:
        """Track agent performance metrics for learning insights."""
        from app.models.agent import Agent

        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return

        config = dict(agent.config) if agent.config else {}
        metrics = config.get("metrics", {"total": 0, "success": 0, "fail": 0})
        metrics["total"] = metrics.get("total", 0) + 1

        if result_data.get("status") == "completed":
            metrics["success"] = metrics.get("success", 0) + 1
        else:
            metrics["fail"] = metrics.get("fail", 0) + 1

        metrics["success_rate"] = round(
            metrics["success"] / max(metrics["total"], 1), 2
        )

        # Track cost and duration averages
        if result_data.get("cost_usd"):
            total_cost = config.get("total_cost_usd", 0) + result_data["cost_usd"]
            config["total_cost_usd"] = round(total_cost, 4)
        if result_data.get("duration_ms"):
            durations = config.get("task_durations", [])
            durations.append(result_data["duration_ms"])
            # Keep last 50 durations for avg calculation
            config["task_durations"] = durations[-50:]
            config["avg_duration_ms"] = round(
                sum(config["task_durations"]) / len(config["task_durations"])
            )

        config["metrics"] = metrics
        agent.config = config

    async def _update_schedule_stats(self, schedule_id: str, result_data: dict) -> None:
        """Update schedule success/fail counts after task completion."""
        from app.models.schedule import Schedule

        result = await self.db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )
        schedule = result.scalar_one_or_none()
        if not schedule:
            return

        if result_data.get("status") == "completed":
            schedule.success_count += 1
        else:
            schedule.fail_count += 1

    async def list_tasks(
        self, status: TaskStatus | None = None, agent_id: str | None = None
    ) -> list[Task]:
        query = select(Task).order_by(Task.created_at.desc())
        if status:
            query = query.where(Task.status == status)
        if agent_id:
            query = query.where(Task.agent_id == agent_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_task(self, task_id: str) -> Task | None:
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()
