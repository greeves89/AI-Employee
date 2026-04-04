import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.models.task import Task, TaskStatus
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)


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

        # Budget check: if task targets a specific agent, verify budget
        if agent_id:
            await self._check_agent_budget(agent_id)

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

        # Publish activity event
        await self._publish_activity(agent_id, f"Task queued: {title} (priority: {priority})")

        await self.db.refresh(task)
        return task

    async def _publish_activity(self, agent_id: str, message: str) -> None:
        """Publish an activity event to the agent's log channel + history."""
        try:
            event = json.dumps({
                "agent_id": agent_id,
                "task_id": "",
                "type": "system",
                "data": {"message": message},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if self.redis.client:
                await self.redis.client.publish(f"agent:{agent_id}:logs", event)
                history_key = f"agent:{agent_id}:activity"
                await self.redis.client.rpush(history_key, event)
                await self.redis.client.ltrim(history_key, -200, -1)
        except Exception as e:
            logger.warning(f"Could not publish activity for agent {agent_id}: {e}")

    async def handle_task_start(self, data: dict) -> None:
        """Update task status to RUNNING when agent picks it up."""
        task_id = data["task_id"]
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task or task.status not in (TaskStatus.QUEUED, TaskStatus.PENDING):
            return
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)
        await self.db.commit()
        logger.info(f"Task {task_id} is now running on agent {data.get('agent_id')}")

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task. Only non-running tasks can be deleted."""
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return False
        if task.status == TaskStatus.RUNNING:
            raise ValueError("Cannot delete a running task. Cancel it first.")
        # If task is queued, remove from Redis queue
        if task.status == TaskStatus.QUEUED and task.agent_id:
            await self._remove_from_queue(task.agent_id, task_id)
        await self.db.delete(task)
        await self.db.commit()
        return True

    async def cancel_task(self, task_id: str) -> Task | None:
        """Cancel a queued/pending task."""
        result = await self.db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return None
        if task.status not in (TaskStatus.QUEUED, TaskStatus.PENDING):
            raise ValueError(f"Cannot cancel a task with status '{task.status.value}'")
        if task.agent_id:
            await self._remove_from_queue(task.agent_id, task_id)
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def _remove_from_queue(self, agent_id: str, task_id: str) -> None:
        """Remove a specific task from an agent's Redis queue."""
        try:
            queue_name = f"agent:{agent_id}:tasks"
            if self.redis.client:
                queue_items = await self.redis.client.lrange(queue_name, 0, -1)
                for item in queue_items:
                    try:
                        payload = json.loads(item)
                        if payload.get("id") == task_id:
                            await self.redis.client.lrem(queue_name, 1, item)
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception as e:
            logger.warning(f"Could not remove task {task_id} from Redis queue: {e}")

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
            # Check budget thresholds after cost update
            await self._check_budget_thresholds(agent_id)

        # Update schedule stats if this task belongs to a schedule
        schedule_id = (task.metadata_ or {}).get("schedule_id")
        if schedule_id:
            await self._update_schedule_stats(schedule_id, data)

        await self.db.commit()

        # Request user rating via notification + Telegram inline keyboard
        if task.status == TaskStatus.COMPLETED:
            await self._request_task_rating(task, agent_id)

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

    async def recover_stale_tasks(self, stale_minutes: int = 10) -> int:
        """Recover tasks stuck as QUEUED/RUNNING after orchestrator restart.

        Tasks can get stuck when the orchestrator misses Redis PubSub completion
        events (e.g., during a restart). This method:
        - Re-queues tasks that are still QUEUED but missing from Redis
        - Marks old RUNNING tasks as failed (agent likely crashed)
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
        recovered = 0

        # Find tasks stuck as QUEUED for too long
        result = await self.db.execute(
            select(Task).where(
                Task.status == TaskStatus.QUEUED,
                Task.created_at < cutoff,
            )
        )
        stale_queued = list(result.scalars().all())

        for task in stale_queued:
            # Check if the task is still in the agent's Redis queue
            if task.agent_id:
                queue_depth = await self.redis.get_queue_depth(task.agent_id)
                if queue_depth > 0:
                    continue  # Task might still be waiting in queue

                # Check if the agent is still alive
                agent_status = await self.redis.get_agent_status(task.agent_id)
                if agent_status.get("state") == "working" and agent_status.get("current_task") == task.id:
                    continue  # Agent is currently working on this task

            # Task is stuck - mark as failed with explanation
            task.status = TaskStatus.FAILED
            task.error = "Task lost during orchestrator restart (completion event missed)"
            task.completed_at = datetime.now(timezone.utc)
            recovered += 1
            logger.info(f"Recovered stale QUEUED task {task.id} (agent: {task.agent_id})")

        # Find tasks stuck as RUNNING for too long
        result = await self.db.execute(
            select(Task).where(
                Task.status == TaskStatus.RUNNING,
                Task.started_at < cutoff,
            )
        )
        stale_running = list(result.scalars().all())

        for task in stale_running:
            # Check if the agent is still working on it
            if task.agent_id:
                agent_status = await self.redis.get_agent_status(task.agent_id)
                if agent_status.get("state") == "working" and agent_status.get("current_task") == task.id:
                    continue  # Agent is still working

            task.status = TaskStatus.FAILED
            task.error = "Task lost - agent stopped responding"
            task.completed_at = datetime.now(timezone.utc)
            recovered += 1
            logger.info(f"Recovered stale RUNNING task {task.id} (agent: {task.agent_id})")

        if recovered:
            await self.db.commit()
            logger.info(f"Recovered {recovered} stale tasks total")

        return recovered

    async def _check_agent_budget(self, agent_id: str) -> None:
        """Raise ValueError if the agent has exceeded its budget."""
        from app.models.agent import Agent

        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent or agent.budget_usd is None:
            return  # No budget limit

        total_cost = (agent.config or {}).get("total_cost_usd", 0)
        if total_cost >= agent.budget_usd:
            raise ValueError(
                f"Agent '{agent.name}' budget exceeded (${total_cost:.2f}/${agent.budget_usd:.2f})"
            )

    async def _check_budget_thresholds(self, agent_id: str) -> None:
        """After task completion, check if budget thresholds are reached."""
        from app.models.agent import Agent

        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent or agent.budget_usd is None:
            return

        total_cost = (agent.config or {}).get("total_cost_usd", 0)
        pct = total_cost / agent.budget_usd if agent.budget_usd > 0 else 0

        if pct >= 1.0:
            # Budget exceeded - send notification
            await self._send_budget_notification(
                agent, total_cost, "exceeded",
                f"Agent '{agent.name}' has exceeded its budget "
                f"(${total_cost:.2f}/${agent.budget_usd:.2f}). "
                f"New tasks will be blocked until the budget is increased.",
            )
        elif pct >= 0.8:
            # Approaching budget
            await self._send_budget_notification(
                agent, total_cost, "warning",
                f"Agent '{agent.name}' is approaching its budget limit "
                f"(${total_cost:.2f}/${agent.budget_usd:.2f}, {pct:.0%} used).",
            )

    async def _send_budget_notification(
        self, agent, total_cost: float, level: str, message: str
    ) -> None:
        """Create a notification for budget alerts."""
        try:
            from app.models.notification import Notification

            notif = Notification(
                agent_id=agent.id,
                type="warning" if level == "warning" else "error",
                title=f"Budget {'Warning' if level == 'warning' else 'Exceeded'}: {agent.name}",
                message=message,
                priority="high",
                action_url=f"/agents/{agent.id}",
            )
            self.db.add(notif)
        except Exception as e:
            logger.warning(f"Could not create budget notification: {e}")

    async def _request_task_rating(self, task: Task, agent_id: str | None) -> None:
        """Send a rating request via Telegram inline keyboard after task completion."""
        try:
            from app.models.notification import Notification

            # Create UI notification with rating action
            notif = Notification(
                agent_id=agent_id or "system",
                type="info",
                title="Task abgeschlossen — Bewertung?",
                message=f"Task \"{task.title}\" ist fertig. Wie war das Ergebnis?",
                priority="normal",
                action_url=f"/tasks/{task.id}",
                meta={"type": "rating_request", "task_id": task.id},
            )
            self.db.add(notif)

            # Send Telegram inline keyboard with star ratings
            await self._send_rating_keyboard(task)
        except Exception as e:
            logger.warning(f"Could not send rating request for task {task.id}: {e}")

    async def _send_rating_keyboard(self, task: Task) -> None:
        """Send Telegram inline keyboard with ⭐1-5 rating buttons."""
        try:
            import json
            title = (task.title or "Task")[:50]
            cost_info = f" (${task.cost_usd:.3f})" if task.cost_usd else ""
            text = f"✅ Task erledigt: {title}{cost_info}\nWie bewertest du das Ergebnis?"
            keyboard = {
                "inline_keyboard": [[
                    {"text": "⭐1", "callback_data": f"rate:{task.id}:1"},
                    {"text": "⭐2", "callback_data": f"rate:{task.id}:2"},
                    {"text": "⭐3", "callback_data": f"rate:{task.id}:3"},
                    {"text": "⭐4", "callback_data": f"rate:{task.id}:4"},
                    {"text": "⭐5", "callback_data": f"rate:{task.id}:5"},
                ]]
            }
            # Publish rating request to Redis for Telegram bot to pick up
            await self.redis.client.publish(
                "telegram:rating_request",
                json.dumps({
                    "text": text,
                    "reply_markup": keyboard,
                    "task_id": task.id,
                }),
            )
        except Exception as e:
            logger.warning(f"Could not send rating keyboard: {e}")
