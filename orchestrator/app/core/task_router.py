import json
import logging
import random
import string
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.load_balancer import LoadBalancer
from app.models.approval_rule import ApprovalRule
from app.models.task import Task, TaskStatus, is_terminal_task_status
from app.services.redis_service import RedisService

# Eviction grace period for completed tasks (seconds)
TASK_EVICT_GRACE_SECONDS = int(300)

# Model used for self-reflection rating + improvement suggestions
_REFLECTION_MODEL = "claude-haiku-4-5-20251001"

_ID_ALPHABET = string.digits + string.ascii_lowercase


def _make_task_id() -> str:
    """Generate a prefixed task ID: t{8 random alphanum chars}.

    Prefix 't' makes tasks instantly recognizable in logs.
    ~2.8 trillion combinations resist brute-force enumeration.
    """
    suffix = "".join(random.choices(_ID_ALPHABET, k=8))
    return f"t{suffix}"

logger = logging.getLogger(__name__)


def _compute_formula_rating(task: "Task") -> int:  # noqa: F821
    """Fallback: compute a mechanical 1-5 star rating from task outcome metrics.

    Formula:
      - Base score: 5 for completed, 2 for failed
      - duration_ms > 120_000 → -1
      - num_turns > 20        → -1
      - cost_usd > 0.10       → -1
      - Clamped to [1, 5]
    """
    from app.models.task import TaskStatus as _TaskStatus

    score = 5 if task.status == _TaskStatus.COMPLETED else 2
    if task.duration_ms is not None and task.duration_ms > 120_000:
        score -= 1
    if task.num_turns is not None and task.num_turns > 20:
        score -= 1
    if task.cost_usd is not None and task.cost_usd > 0.10:
        score -= 1
    return max(1, min(5, score))


async def _llm_reflect_on_task(task: "Task") -> tuple[int, str]:  # noqa: F821
    """Ask Claude CLI to self-reflect on a completed task and return (rating, reflection).

    Uses `claude -p` subprocess so it works with both API key and OAuth token.
    Falls back to formula rating if the call fails.
    """
    import asyncio, os, shutil
    from app.config import settings

    if not shutil.which("claude"):
        return _compute_formula_rating(task), "auto-rated (claude CLI not found)"

    duration_s = round((task.duration_ms or 0) / 1000, 1)
    prompt = (
        "Rate this AI agent task 1-5 stars and give a one-sentence reflection.\n\n"
        f"Task: {task.title or 'Untitled'}\n"
        f"Status: {task.status.value}\n"
        f"Duration: {duration_s}s | Turns: {task.num_turns or 0} | Cost: ${task.cost_usd or 0:.4f}\n"
        f"Error: {task.error or 'none'}\n\n"
        'Respond with JSON only: {"rating": <1-5>, "reflection": "<one sentence>"}'
    )

    try:
        env = os.environ.copy()
        if settings.anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
        elif settings.claude_code_oauth_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token

        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            "--output-format", "json",
            "--model", _REFLECTION_MODEL,
            "--max-turns", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
        output = json.loads(stdout.decode())
        text = output.get("result", "")
        # Strip markdown fences
        text = text.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:]).split("```")[0]
        parsed = json.loads(text)
        rating = max(1, min(5, int(parsed["rating"])))
        reflection = str(parsed.get("reflection", ""))[:500]
        return rating, reflection
    except Exception as exc:
        logger.warning(f"LLM self-reflection failed for task {task.id}, falling back to formula: {exc}")
        return _compute_formula_rating(task), "auto-rated (formula fallback)"


async def _build_approval_rules_prefix(db: AsyncSession, agent_id: str) -> str:
    """Build a prompt prefix that lists all active approval rules for the agent.

    The agent is told to call the `request_approval` MCP tool whenever a rule matches
    before taking the action.
    """
    try:
        result = await db.execute(
            select(ApprovalRule)
            .where(ApprovalRule.is_active == True)
            .where((ApprovalRule.agent_id.is_(None)) | (ApprovalRule.agent_id == agent_id))
        )
        rules = list(result.scalars().all())
    except Exception:
        return ""

    if not rules:
        return ""

    lines = [
        "",
        "=== APPROVAL RULES (MANDATORY) ===",
        "The user has defined these rules. You MUST call `request_approval` BEFORE acting",
        "whenever any of these rules apply to what you are about to do:",
        "",
    ]
    for r in rules:
        threshold_str = f" (threshold: {r.threshold})" if r.threshold is not None else ""
        lines.append(f"  [{r.category}] {r.name}{threshold_str}: {r.description}")
    lines.extend([
        "",
        "If unsure whether a rule applies, ASK via request_approval. Better safe than sorry.",
        "=== END APPROVAL RULES ===",
        "",
    ])
    return "\n".join(lines)


class TaskRouter:
    """Routes tasks to agents via Redis queues with load balancing."""

    def __init__(self, db: AsyncSession, redis: RedisService, load_balancer: LoadBalancer, docker_service=None):
        self.db = db
        self.redis = redis
        self.load_balancer = load_balancer
        self.docker = docker_service

    async def create_and_route_task(
        self,
        title: str,
        prompt: str,
        priority: int = 1,
        agent_id: str | None = None,
        model: str | None = None,
        parent_task_id: str | None = None,
        created_by_agent: str | None = None,
    ) -> Task:
        task_id = _make_task_id()

        # Platform-wide budget check
        await self._check_platform_budget()

        # Budget check: if task targets a specific agent, verify budget
        if agent_id:
            await self._check_agent_budget(agent_id)

        # Auto-assign if no agent specified
        if not agent_id:
            agent_id = await self.load_balancer.select_agent(priority=priority)
            if not agent_id:
                # No agents available - create task as pending
                metadata = {}
                if created_by_agent:
                    metadata["created_by_agent"] = created_by_agent
                task = Task(
                    id=task_id,
                    title=title,
                    prompt=prompt,
                    status=TaskStatus.PENDING,
                    priority=priority,
                    model=model,
                    parent_task_id=parent_task_id,
                    metadata_=metadata or None,
                )
                self.db.add(task)
                await self.db.commit()
                await self.db.refresh(task)
                return task

        # Create task in DB
        metadata = {}
        if created_by_agent:
            metadata["created_by_agent"] = created_by_agent
        task = Task(
            id=task_id,
            title=title,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            priority=priority,
            agent_id=agent_id,
            model=model,
            parent_task_id=parent_task_id,
            metadata_=metadata or None,
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(task)
        await self.db.commit()

        # Inject active approval rules into the prompt
        rules_prefix = await _build_approval_rules_prefix(self.db, agent_id)
        final_prompt = rules_prefix + prompt if rules_prefix else prompt

        # Wake agent if stopped (auto-lifecycle)
        if self.docker:
            try:
                from app.services.user_lifecycle import wake_agent
                await wake_agent(self.db, self.docker, agent_id)
            except Exception as e:
                logger.warning(f"Could not wake agent {agent_id} before task: {e}")

        # Push to agent's Redis queue
        task_payload = json.dumps(
            {
                "id": task_id,
                "prompt": final_prompt,
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
        # Mark as notified → schedule eviction (unless UI is retaining it)
        task.notified = True
        if not task.retain:
            task.evict_after = datetime.now(timezone.utc) + timedelta(seconds=TASK_EVICT_GRACE_SECONDS)

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

        # Auto-rate the task based on outcome metrics
        await self._auto_rate_task(task)

        # Track skill usage for installed skills on this agent
        if agent_id and task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            await self._record_skill_usages(task, agent_id)

        # Subtask completion callback: notify the parent task's agent
        if task.parent_task_id:
            await self._notify_parent_agent(task)

        # Delegation callback: notify the agent that created/delegated this task
        delegator_id = (task.metadata_ or {}).get("created_by_agent")
        print(f"[TaskCompletion] Task {task.id}: metadata={task.metadata_}, delegator={delegator_id}, agent={agent_id}")
        if delegator_id and delegator_id != agent_id:
            print(f"[TaskCompletion] Firing delegation callback for task {task.id} → delegator {delegator_id}")
            await self._notify_delegating_agent(task, delegator_id)

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
        self,
        status: TaskStatus | None = None,
        agent_id: str | None = None,
        agent_ids: list[str] | None = None,
    ) -> list[Task]:
        query = select(Task).order_by(Task.created_at.desc())
        if status:
            query = query.where(Task.status == status)
        if agent_id:
            query = query.where(Task.agent_id == agent_id)
        elif agent_ids is not None:
            query = query.where(Task.agent_id.in_(agent_ids))
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

    async def _auto_rate_task(self, task: Task) -> None:
        """Self-reflect on a completed/failed task via LLM and persist the rating.

        Sends task metadata to claude-haiku for a 1-5 star rating + one-sentence
        reflection. Falls back to the formula-based rating if the LLM call fails.
        """
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return
        if not task.agent_id:
            return
        try:
            from app.models.task_rating import TaskRating

            rating, reflection = await _llm_reflect_on_task(task)
            task_rating = TaskRating(
                task_id=task.id,
                agent_id=task.agent_id,
                user_id=None,  # system-generated
                rating=rating,
                comment=reflection,
                task_cost_usd=task.cost_usd,
                task_duration_ms=task.duration_ms,
                task_num_turns=task.num_turns,
            )
            self.db.add(task_rating)
            await self.db.commit()
            logger.info(
                f"Self-reflected task {task.id} → {rating}/5: {reflection!r} "
                f"(status={task.status.value}, duration_ms={task.duration_ms}, "
                f"num_turns={task.num_turns}, cost_usd={task.cost_usd})"
            )

            # Trigger improvement engine every 10th completed task per agent
            await self._maybe_trigger_improvement(task.agent_id)
        except Exception as e:
            logger.warning(f"Could not auto-rate task {task.id}: {e}")

    async def _record_skill_usages(self, task: Task, agent_id: str) -> None:
        """Record SkillTaskUsage for every skill installed on this agent at task completion."""
        try:
            from app.models.skill import AgentSkillAssignment, Skill, SkillTaskUsage
            assignments = list((await self.db.execute(
                select(AgentSkillAssignment).where(AgentSkillAssignment.agent_id == agent_id)
            )).scalars().all())
            if not assignments:
                return
            skill_ids = [a.skill_id for a in assignments]
            skills = list((await self.db.execute(
                select(Skill).where(Skill.id.in_(skill_ids))
            )).scalars().all())
            for skill in skills:
                usage = SkillTaskUsage(
                    skill_id=skill.id,
                    task_id=task.id,
                    agent_id=agent_id,
                    task_duration_ms=task.duration_ms,
                    task_cost_usd=task.cost_usd,
                )
                self.db.add(usage)
                skill.usage_count = (skill.usage_count or 0) + 1
                skill.avg_agent_duration_ms = (
                    int(task.duration_ms)
                    if not skill.avg_agent_duration_ms
                    else int((skill.avg_agent_duration_ms * 0.8 + (task.duration_ms or 0) * 0.2))
                )
            await self.db.commit()
        except Exception as e:
            logger.warning(f"Could not record skill usages for task {task.id}: {e}")

    async def _maybe_trigger_improvement(self, agent_id: str) -> None:
        """Trigger the improvement engine every 10th rated task for an agent."""
        try:
            from app.models.task_rating import TaskRating
            from sqlalchemy import func as sa_func

            result = await self.db.execute(
                select(sa_func.count()).select_from(TaskRating).where(TaskRating.agent_id == agent_id)
            )
            count = result.scalar() or 0
            if count > 0 and count % 10 == 0:
                from app.services.improvement_engine import ImprovementEngine
                engine = ImprovementEngine()
                await engine.analyze(agent_id, self.db)
        except Exception as e:
            logger.warning(f"Could not trigger improvement engine for agent {agent_id}: {e}")

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

    async def _check_platform_budget(self) -> None:
        """Raise ValueError if the platform-wide spending limit is exceeded."""
        from app.config import settings

        cap = settings.platform_budget_usd
        if not cap or cap <= 0:
            return  # No platform budget configured

        from app.models.task import Task as TaskModel
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.coalesce(func.sum(TaskModel.cost_usd), 0)).where(
                TaskModel.cost_usd.isnot(None),
                TaskModel.created_at >= datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0),
            )
        )
        monthly_spend = float(result.scalar() or 0)
        if monthly_spend >= cap:
            raise ValueError(
                f"Platform monthly budget exceeded (${monthly_spend:.2f}/${cap:.2f}). "
                f"Increase PLATFORM_BUDGET_USD or wait for next month."
            )

    async def _notify_parent_agent(self, subtask: Task) -> None:
        """When a subtask completes, notify the parent task's agent via message queue.

        Sends two types of notifications:
        1. Per-subtask: immediate notification with this subtask's result
        2. Batch-complete: when ALL sibling subtasks are done, sends an aggregated summary
        """
        try:
            parent = await self.db.execute(
                select(Task).where(Task.id == subtask.parent_task_id)
            )
            parent_task = parent.scalar_one_or_none()
            if not parent_task or not parent_task.agent_id:
                return

            status = "completed" if subtask.status == TaskStatus.COMPLETED else "failed"
            result_preview = (subtask.result or subtask.error or "")[:500]

            # 1. Per-subtask notification
            message = json.dumps({
                "type": "subtask_completed",
                "subtask_id": subtask.id,
                "subtask_title": subtask.title,
                "status": status,
                "result_preview": result_preview,
                "cost_usd": subtask.cost_usd,
                "parent_task_id": subtask.parent_task_id,
            })

            if self.redis.client:
                await self.redis.client.lpush(
                    f"agent:{parent_task.agent_id}:messages", message
                )
                logger.info(
                    f"Subtask {subtask.id} ({status}) → notified parent agent "
                    f"{parent_task.agent_id} (parent task {parent_task.id})"
                )

            # 2. Check if ALL sibling subtasks are now done → send aggregated summary
            from sqlalchemy import func as sa_func
            siblings = await self.db.execute(
                select(Task).where(Task.parent_task_id == subtask.parent_task_id)
            )
            all_siblings = list(siblings.scalars().all())
            terminal = [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
            all_done = all(s.status in terminal for s in all_siblings)

            if all_done and len(all_siblings) > 1:
                completed = sum(1 for s in all_siblings if s.status == TaskStatus.COMPLETED)
                failed = sum(1 for s in all_siblings if s.status == TaskStatus.FAILED)
                total_cost = sum(s.cost_usd or 0 for s in all_siblings)

                summaries = []
                for s in all_siblings:
                    s_status = "completed" if s.status == TaskStatus.COMPLETED else "failed"
                    s_preview = (s.result or s.error or "")[:200]
                    summaries.append({
                        "id": s.id, "title": s.title,
                        "status": s_status, "result_preview": s_preview,
                    })

                batch_message = json.dumps({
                    "type": "all_subtasks_completed",
                    "parent_task_id": subtask.parent_task_id,
                    "total": len(all_siblings),
                    "completed": completed,
                    "failed": failed,
                    "total_cost_usd": round(total_cost, 4),
                    "subtasks": summaries,
                })

                if self.redis.client:
                    await self.redis.client.lpush(
                        f"agent:{parent_task.agent_id}:messages", batch_message
                    )
                    logger.info(
                        f"ALL {len(all_siblings)} subtasks done for parent {parent_task.id} "
                        f"→ sent aggregated summary to agent {parent_task.agent_id} "
                        f"({completed} OK, {failed} failed)"
                    )
        except Exception as e:
            logger.warning(f"Could not notify parent agent for subtask {subtask.id}: {e}")

    async def _notify_delegating_agent(self, task: Task, delegator_agent_id: str) -> None:
        """When a delegated task completes, notify the agent that created it.

        Pushes a structured message to the delegating agent's chat queue so it
        sees the result in its next chat session or proactive run. Also sends
        a Telegram notification to the user for immediate visibility.
        """
        try:
            status = "completed" if task.status == TaskStatus.COMPLETED else "failed"
            result_preview = (task.result or task.error or "No output")[:800]

            # Push structured message to delegating agent's message queue
            message = json.dumps({
                "type": "delegated_task_completed",
                "task_id": task.id,
                "task_title": task.title,
                "status": status,
                "assigned_agent_id": task.agent_id,
                "result_preview": result_preview,
                "cost_usd": task.cost_usd,
                "duration_ms": task.duration_ms,
            })

            if self.redis.client:
                print(f"[DelegationCallback] Pushing to agent:{delegator_agent_id}:messages")
                await self.redis.client.lpush(
                    f"agent:{delegator_agent_id}:messages", message
                )
                print(f"[DelegationCallback] Message pushed OK")

                # Also push to the delegating agent's chat queue so it
                # picks up the result in the next chat interaction.
                # Format must match what the agent runner expects: {id, text}
                callback_id = uuid.uuid4().hex[:12]
                status_emoji = "✅" if status == "completed" else "❌"
                chat_notification = json.dumps({
                    "id": callback_id,
                    "text": (
                        f"{status_emoji} [Delegation Result] Task '{task.title}' "
                        f"(#{task.id}) has {status}.\n"
                        f"Result: {result_preview[:300]}"
                    ),
                    "session_id": "delegation-callback",
                })
                await self.redis.client.lpush(
                    f"agent:{delegator_agent_id}:chat", chat_notification
                )

                logger.info(
                    f"Delegated task {task.id} ({status}) → notified delegating "
                    f"agent {delegator_agent_id}"
                )

            # Send Telegram notification to the user for immediate visibility
            try:
                status_emoji = "✅" if status == "completed" else "❌"
                title = (task.title or "Task")[:60]
                cost_info = f" (${task.cost_usd:.3f})" if task.cost_usd else ""
                telegram_text = (
                    f"{status_emoji} Delegierter Task {status}: {title}{cost_info}"
                )
                if self.redis.client:
                    await self.redis.client.publish(
                        "telegram:send",
                        json.dumps({"text": telegram_text}),
                    )
            except Exception:
                pass  # Telegram is best-effort

        except Exception as e:
            logger.warning(
                f"Could not notify delegating agent {delegator_agent_id} "
                f"for task {task.id}: {e}"
            )

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
