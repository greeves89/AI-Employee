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
from app.services.skill_auto_injector import auto_inject_skills

# Eviction grace period for completed tasks (seconds)
TASK_EVICT_GRACE_SECONDS = int(300)

# Model used for self-reflection rating + improvement suggestions
_REFLECTION_MODEL = "claude-haiku-4-5-20251001"

# Cheap model an agent is downgraded to once its monthly budget is exhausted
# (when budget_exceeded_action == "haiku").
BUDGET_FALLBACK_MODEL = "claude-haiku-4-5-20251001"

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
        metadata: dict | None = None,
    ) -> Task:
        task_id = _make_task_id()

        # Platform-wide budget check
        await self._check_platform_budget()

        # Auto-assign if no agent specified
        if not agent_id:
            agent_id = await self.load_balancer.select_agent(priority=priority)
            if agent_id and not await self._agent_exists(agent_id):
                logger.warning(
                    "Load balancer selected stale agent %s; pruning Redis status and retrying",
                    agent_id,
                )
                if self.redis.client:
                    await self.redis.client.delete(f"agent:{agent_id}:status")
                agent_id = await self.load_balancer.select_agent(priority=priority)
                if agent_id and not await self._agent_exists(agent_id):
                    logger.warning("Load balancer retry also selected invalid agent %s", agent_id)
                    agent_id = None
            if not agent_id:
                # No agents available - create task as pending
                task_metadata = dict(metadata or {})
                if created_by_agent:
                    task_metadata["created_by_agent"] = created_by_agent
                task = Task(
                    id=task_id,
                    title=title,
                    prompt=prompt,
                    status=TaskStatus.PENDING,
                    priority=priority,
                    model=model,
                    parent_task_id=parent_task_id,
                    metadata_=task_metadata or {},
                )
                self.db.add(task)
                await self.db.commit()
                await self.db.refresh(task)
                return task

        if not await self._agent_exists(agent_id):
            logger.warning("Cannot route task %s to missing agent %s", task_id, agent_id)
            task = Task(
                id=task_id,
                title=title,
                prompt=prompt,
                status=TaskStatus.PENDING,
                priority=priority,
                model=model,
                parent_task_id=parent_task_id,
                metadata_=dict(metadata or {}),
            )
            self.db.add(task)
            await self.db.commit()
            await self.db.refresh(task)
            return task

        # Budget enforcement: downgrades the model to Haiku or blocks+stops
        # the agent once its (or its owner's) monthly budget is exhausted.
        model = await self._apply_budget_policy(agent_id, model)

        # Create task in DB
        task_metadata = dict(metadata or {})
        if created_by_agent:
            task_metadata["created_by_agent"] = created_by_agent
        task = Task(
            id=task_id,
            title=title,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            priority=priority,
            agent_id=agent_id,
            model=model,
            parent_task_id=parent_task_id,
            metadata_=task_metadata or {},
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(task)
        await self.db.commit()

        # Auto-inject skills based on task prompt paths and agent role
        try:
            await auto_inject_skills(self.db, agent_id, prompt)
        except Exception as e:
            logger.warning(f"Skill auto-injection failed for task {task_id}: {e}")

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

    async def _agent_exists(self, agent_id: str | None) -> bool:
        if not agent_id:
            return False
        from app.models.agent import Agent

        existing = await self.db.scalar(select(Agent.id).where(Agent.id == agent_id))
        return existing is not None

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
        task.input_tokens = data.get("input_tokens")
        task.output_tokens = data.get("output_tokens")
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
        elif task.status == TaskStatus.FAILED:
            await self._notify_failed_task(task, agent_id)

    async def _recover_task_completion_from_steps(self, task: Task) -> bool:
        """Recover a missed task completion from persisted step history.

        Agent task completion is delivered via Redis Pub/Sub, which is not
        durable. The per-step stream is persisted separately, so if the
        completion event is missed during an orchestrator reload/restart we can
        still reconstruct the terminal state before marking the task as lost.
        """
        from app.models.task_step import TaskStep

        result = await self.db.execute(
            select(TaskStep)
            .where(TaskStep.task_id == task.id)
            .order_by(TaskStep.timestamp.desc())
            .limit(200)
        )
        steps = list(result.scalars().all())
        if not steps:
            return False

        latest_result = next((s for s in steps if s.event_type == "result"), None)
        latest_error = next((s for s in steps if s.event_type == "error"), None)
        latest_text = next((s for s in steps if s.event_type == "text"), None)
        latest_failed_system = next(
            (
                s for s in steps
                if s.event_type == "system"
                and str((s.event_data or {}).get("message", "")).startswith("Task failed:")
            ),
            None,
        )

        if latest_result:
            event_data = dict(latest_result.event_data or {})
            text_data = (latest_text.event_data or {}) if latest_text else {}
            await self.handle_task_completion({
                "task_id": task.id,
                "agent_id": task.agent_id,
                "status": "completed",
                "result": text_data.get("text"),
                "cost_usd": event_data.get("cost_usd"),
                "input_tokens": event_data.get("input_tokens"),
                "output_tokens": event_data.get("output_tokens"),
                "duration_ms": event_data.get("duration_ms"),
                "num_turns": event_data.get("num_turns"),
            })
            logger.info(f"Recovered completed RUNNING task {task.id} from persisted task_steps")
            return True

        failure_step = latest_error or latest_failed_system
        if failure_step:
            event_data = failure_step.event_data or {}
            message = (
                event_data.get("message")
                or event_data.get("error")
                or json.dumps(event_data)[:500]
            )
            await self.handle_task_completion({
                "task_id": task.id,
                "agent_id": task.agent_id,
                "status": "failed",
                "error": message,
            })
            logger.info(f"Recovered failed RUNNING task {task.id} from persisted task_steps")
            return True

        return False

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

        # Track cost, tokens, and duration averages
        if result_data.get("cost_usd"):
            total_cost = config.get("total_cost_usd", 0) + result_data["cost_usd"]
            config["total_cost_usd"] = round(total_cost, 4)
        if result_data.get("input_tokens"):
            config["total_input_tokens"] = config.get("total_input_tokens", 0) + result_data["input_tokens"]
        if result_data.get("output_tokens"):
            config["total_output_tokens"] = config.get("total_output_tokens", 0) + result_data["output_tokens"]
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
        limit: int = 100,
        offset: int = 0,
    ) -> list[Task]:
        query = select(Task).order_by(Task.created_at.desc())
        if status:
            query = query.where(Task.status == status)
        if agent_id:
            query = query.where(Task.agent_id == agent_id)
        elif agent_ids is not None:
            query = query.where(Task.agent_id.in_(agent_ids))
        query = query.offset(offset).limit(limit)
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
            task.notified = True
            if not task.retain:
                task.evict_after = datetime.now(timezone.utc) + timedelta(seconds=TASK_EVICT_GRACE_SECONDS)
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

            if await self._recover_task_completion_from_steps(task):
                recovered += 1
                continue

            task.status = TaskStatus.FAILED
            task.error = "Task lost - agent stopped responding"
            task.completed_at = datetime.now(timezone.utc)
            task.notified = True
            if not task.retain:
                task.evict_after = datetime.now(timezone.utc) + timedelta(seconds=TASK_EVICT_GRACE_SECONDS)
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
        """Backfill timing data on SkillTaskUsage rows created by explicit skill_rate calls.

        We no longer create records for all installed skills — only skills the agent
        explicitly rated via skill_rate matter. This method backfills task_duration_ms
        and task_cost_usd once the task is complete (those values are None at skill_rate
        call time because the task is still running).
        """
        try:
            from app.models.skill import Skill, SkillTaskUsage
            existing = list((await self.db.execute(
                select(SkillTaskUsage).where(
                    SkillTaskUsage.task_id == task.id,
                    SkillTaskUsage.task_duration_ms.is_(None),
                )
            )).scalars().all())
            if not existing:
                return
            skill_ids = {u.skill_id for u in existing}
            skills = {s.id: s for s in (await self.db.execute(
                select(Skill).where(Skill.id.in_(skill_ids))
            )).scalars().all()}
            for usage in existing:
                usage.task_duration_ms = task.duration_ms
                usage.task_cost_usd = task.cost_usd
                skill = skills.get(usage.skill_id)
                if skill and task.duration_ms:
                    skill.avg_agent_duration_ms = (
                        int(task.duration_ms)
                        if not skill.avg_agent_duration_ms
                        else int((skill.avg_agent_duration_ms * 0.8 + task.duration_ms * 0.2))
                    )
                    if skill.manual_duration_seconds:
                        agent_secs = task.duration_ms / 1000
                        usage.time_saved_seconds = max(0, int(skill.manual_duration_seconds - agent_secs))
            await self.db.commit()
        except Exception as e:
            logger.warning(f"Could not backfill skill timings for task {task.id}: {e}")

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

    @staticmethod
    def _month_start() -> datetime:
        """First instant of the current UTC month."""
        return datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

    async def _agent_monthly_cost(self, agent_id: str) -> float:
        """Sum of task cost for one agent in the current calendar month."""
        from sqlalchemy import func

        result = await self.db.execute(
            select(func.coalesce(func.sum(Task.cost_usd), 0)).where(
                Task.agent_id == agent_id,
                Task.cost_usd.isnot(None),
                Task.created_at >= self._month_start(),
            )
        )
        return float(result.scalar() or 0)

    async def _user_monthly_cost(self, user_id: str) -> float:
        """Sum of task cost across all of a user's agents this month."""
        from sqlalchemy import func
        from app.models.agent import Agent

        result = await self.db.execute(
            select(func.coalesce(func.sum(Task.cost_usd), 0))
            .select_from(Task)
            .join(Agent, Task.agent_id == Agent.id)
            .where(
                Agent.user_id == user_id,
                Task.cost_usd.isnot(None),
                Task.created_at >= self._month_start(),
            )
        )
        return float(result.scalar() or 0)

    async def _apply_budget_policy(
        self, agent_id: str, requested_model: str | None
    ) -> str | None:
        """Enforce monthly budgets. Returns the model the task should run with.

        If the agent's monthly budget OR its owner's monthly cap is exhausted:
          - action "haiku": returns the cheap fallback model
          - action "stop":  stops the agent container and raises ValueError
        """
        from app.models.agent import Agent, AgentState
        from app.models.user import User

        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return requested_model

        reason = ""

        if agent.budget_usd is not None and agent.budget_usd > 0:
            spent = await self._agent_monthly_cost(agent_id)
            if spent >= agent.budget_usd:
                reason = f"Agent budget exhausted (${spent:.2f}/${agent.budget_usd:.2f})"

        if not reason and agent.user_id:
            ures = await self.db.execute(select(User).where(User.id == agent.user_id))
            owner = ures.scalar_one_or_none()
            if owner and owner.budget_usd is not None and owner.budget_usd > 0:
                user_spent = await self._user_monthly_cost(agent.user_id)
                if user_spent >= owner.budget_usd:
                    reason = (
                        f"User budget exhausted "
                        f"(${user_spent:.2f}/${owner.budget_usd:.2f})"
                    )

        if not reason:
            return requested_model

        if agent.budget_exceeded_action == "stop":
            agent.state = AgentState.STOPPED
            if self.docker and agent.container_id:
                try:
                    self.docker.stop_container(agent.container_id)
                except Exception as e:
                    logger.warning(
                        f"Could not stop over-budget agent {agent_id}: {e}"
                    )
            await self.db.commit()
            raise ValueError(
                f"{reason}. Agent '{agent.name}' stopped — raise the budget "
                f"or wait for next month."
            )

        # Default action: downgrade to the cheap fallback model.
        logger.info(
            f"[Budget] {reason} → agent {agent_id} downgraded to {BUDGET_FALLBACK_MODEL}"
        )
        return BUDGET_FALLBACK_MODEL

    async def _check_budget_thresholds(self, agent_id: str) -> None:
        """After task completion, check if monthly budget thresholds are reached."""
        from app.models.agent import Agent

        result = await self.db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent or agent.budget_usd is None or agent.budget_usd <= 0:
            return

        total_cost = await self._agent_monthly_cost(agent_id)
        pct = total_cost / agent.budget_usd

        if pct >= 1.0:
            consequence = (
                "The agent has been stopped."
                if agent.budget_exceeded_action == "stop"
                else "New tasks now run on the cheap fallback model (Haiku)."
            )
            await self._send_budget_notification(
                agent, total_cost, "exceeded",
                f"Agent '{agent.name}' has exhausted its monthly budget "
                f"(${total_cost:.2f}/${agent.budget_usd:.2f}). {consequence}",
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
            await self.db.commit()
            await self.db.refresh(notif)
            await self._publish_notification(notif)
            await self._push_notification_to_agent_user(notif, agent_id)

            # Send Telegram inline keyboard with star ratings
            await self._send_rating_keyboard(task)
        except Exception as e:
            logger.warning(f"Could not send rating request for task {task.id}: {e}")

    async def _notify_failed_task(self, task: Task, agent_id: str | None) -> None:
        """Notify the user when a task failed and deep-link to the result."""
        try:
            from app.models.notification import Notification

            message = task.error or f"Task \"{task.title}\" ist fehlgeschlagen."
            notif = Notification(
                agent_id=agent_id or "system",
                type="error",
                title="Task fehlgeschlagen",
                message=str(message)[:240],
                priority="high",
                action_url=f"/tasks/{task.id}",
                meta={"type": "task_failed", "task_id": task.id},
            )
            self.db.add(notif)
            await self.db.commit()
            await self.db.refresh(notif)
            await self._publish_notification(notif)
            await self._push_notification_to_agent_user(notif, agent_id)
        except Exception as e:
            logger.warning(f"Could not send failure notification for task {task.id}: {e}")

    async def _publish_notification(self, notif) -> None:
        if not self.redis or not self.redis.client:
            return
        event = json.dumps({
            "type": "notification",
            "data": self._notification_response(notif),
        })
        await self.redis.client.publish("notifications:live", event)

    async def _push_notification_to_agent_user(
        self,
        notif,
        agent_id: str | None,
    ) -> None:
        if not agent_id:
            return
        try:
            from app.models.agent import Agent
            from app.services.apns_service import push_to_user

            agent = await self.db.scalar(select(Agent).where(Agent.id == agent_id))
            if not agent or not agent.user_id:
                return
            await push_to_user(
                self.db,
                agent.user_id,
                notif.title,
                notif.message or notif.title,
                data=self._notification_push_payload(notif),
            )
        except Exception:
            logger.exception("APNs push failed for task notification")

    def _notification_push_payload(self, notif) -> dict:
        meta = notif.meta or {}
        payload = {
            "notification_id": str(notif.id),
            "agent_id": notif.agent_id,
            "type": notif.type,
            "action_url": notif.action_url or "",
            "meta": meta,
        }
        if isinstance(meta, dict) and meta.get("task_id"):
            payload["task_id"] = str(meta["task_id"])
        return payload

    def _notification_response(self, notif) -> dict:
        return {
            "id": notif.id,
            "agent_id": notif.agent_id,
            "type": notif.type,
            "title": notif.title,
            "message": notif.message,
            "priority": notif.priority,
            "read": notif.read,
            "action_url": notif.action_url,
            "meta": notif.meta,
            "created_at": notif.created_at.isoformat() if notif.created_at else "",
        }

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
                if self.redis.client and delegator_agent_id:
                    await self.redis.client.publish(
                        f"agent:{delegator_agent_id}:telegram:send",
                        json.dumps({"text": telegram_text, "agent_id": delegator_agent_id}),
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
