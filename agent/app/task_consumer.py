import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher
from app.pids_budget import (
    DEFAULT_COST_PER_RUN,
    DEFAULT_RESERVE,
    max_concurrent_runs,
    read_pids_limits,
)

logger = logging.getLogger(__name__)


def _max_parallel_tasks() -> int:
    """How many tasks ONE agent runs at the same time. Default 1 = serial (unchanged
    behaviour). Set MAX_PARALLEL_TASKS>1 to let independent tasks run concurrently —
    each in its own runner subprocess. Mirrors MAX_PARALLEL_CHATS on the chat side.

    Der Wunsch aus ``MAX_PARALLEL_TASKS`` ist eine Obergrenze, keine Zusage: was
    nicht ins pids-Budget des Containers passt, wird gedeckelt. Sonst startet der
    Agent mehr Laeufe, als der Kernel Prozesse hergibt — und ab da scheitert jedes
    ``gh``/``git``/``pytest`` still (Issue #628)."""
    try:
        wanted = max(1, int(os.getenv("MAX_PARALLEL_TASKS", "1")))
    except (TypeError, ValueError):
        wanted = 1

    budget = max_concurrent_runs(
        reserve=_env_int("PIDS_RESERVE", DEFAULT_RESERVE),
        cost_per_run=_env_int("PIDS_COST_PER_RUN", DEFAULT_COST_PER_RUN),
    )
    if wanted > budget:
        logger.warning(
            "MAX_PARALLEL_TASKS=%d passt nicht ins pids-Budget des Containers — "
            "gedeckelt auf %d (siehe Issue #628)",
            wanted, budget,
        )
        return budget
    return wanted


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


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
        # IDs ALLER gerade laufenden Aufgaben. `current_task` kann nur eine nennen —
        # der Orchestrator hielt die uebrigen deshalb faelschlich fuer verschollen
        # und raeumte sie mitten in der Arbeit ab.
        self._active_task_ids: set[str] = set()
        #: Aufgabe -> ihr Runner. Ohne diese Zuordnung liess sich eine EINZELNE
        #: laufende Aufgabe nicht stoppen: `stop()` kannte nur „alle Runner".
        #: Genau daran scheiterte das Abbrechen per Sprache — der Nutzer sagte
        #: dreimal „abbrechen", bekam dreimal „ist gestoppt", und die Aufgabe
        #: lief weiter (gemeldet am 21.08.2026).
        self._runner_by_task: dict[str, object] = {}

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
        current, limit = read_pids_limits()
        logger.info(
            "Task parallelism: %d concurrent (pids %s/%s)",
            max_parallel,
            current if current is not None else "?",
            limit if limit is not None else "?",
        )

        # Der Abbruch-Zuhoerer laeuft neben der Warteschlange — sonst koennte er
        # erst dran kommen, wenn gerade keine Aufgabe verarbeitet wird, also
        # genau dann nicht, wenn man ihn braucht.
        abbruch = asyncio.create_task(self._cancel_listener())
        self._inflight.add(abbruch)
        abbruch.add_done_callback(self._inflight.discard)

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
        if task_id:
            self._runner_by_task[task_id] = runner
        # Any task working → agent shows "working"; when the last finishes we go idle.
        try:
            if task_id:
                self._active_task_ids.add(task_id)
            await self._log_publisher.publish_status(
                "working", task_id, active_sessions=sorted(self._active_task_ids)
            )
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
            if task_id:
                self._runner_by_task.pop(task_id, None)
            self._active_task_ids.discard(task_id)
            # Only flip to idle when no other task is still running.
            try:
                if not self._active_runners:
                    await self._log_publisher.publish_status("idle")
                else:
                    # Andere laufen weiter — die Liste muss stimmen, sonst gilt eine
                    # noch arbeitende Aufgabe beim Orchestrator als verschollen.
                    await self._log_publisher.publish_status(
                        "working", "", active_sessions=sorted(self._active_task_ids)
                    )
            except Exception:  # noqa: BLE001
                pass
            self._sem.release()

    async def _cancel_listener(self) -> None:
        """Auf „stopp diese Aufgabe" hoeren.

        Der Kanal ``agent:{id}:task:cancel`` wurde vom Orchestrator seit jeher
        BESENDET — nur hat ihm nie jemand zugehoert. Ein Abbruch erreichte
        deshalb ausschliesslich Chat-Zuege (ueber ``chat:cancel``), waehrend
        eingeplante Aufgaben unbeirrt weiterliefen. Der Nutzer sagte am
        21.08.2026 dreimal „abbrechen", bekam dreimal „ist gestoppt" — und sah
        die Aufgabe weiterlaufen.

        Nutzlast ist die Aufgabenkennung; ``all`` stoppt alles Laufende.
        """
        kanal = f"agent:{self.agent_id}:task:cancel"
        verbindung = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = verbindung.pubsub()
        await pubsub.subscribe(kanal)
        logger.info("Auf Aufgaben-Abbrueche horchend: %s", kanal)
        try:
            while self.running:
                nachricht = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not nachricht or nachricht.get("type") != "message":
                    await asyncio.sleep(0.05)
                    continue
                wen = str(nachricht.get("data") or "").strip()
                ziele = (
                    list(self._runner_by_task.items()) if wen in ("", "all")
                    else [(wen, self._runner_by_task.get(wen))]
                )
                for tid, runner in ziele:
                    if not runner:
                        logger.info("Abbruch fuer %s: laeuft hier nicht (mehr)", tid)
                        continue
                    try:
                        if getattr(runner, "is_running", False):
                            await runner.interrupt()
                            logger.info("Aufgabe %s auf Zuruf gestoppt", tid)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Aufgabe %s liess sich nicht stoppen: %s", tid, e)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — der Zuhoerer darf den Agenten nie mitreissen
            logger.warning("Abbruch-Zuhoerer beendet", exc_info=True)
        finally:
            try:
                await pubsub.unsubscribe(kanal)
                await pubsub.aclose()
                await verbindung.aclose()
            except Exception:  # noqa: BLE001
                pass

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
