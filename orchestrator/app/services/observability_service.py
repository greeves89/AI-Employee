"""LLM-Observability (Langfuse) — the SINGLE place that talks to Langfuse.

Design goals:
- Verzahnt, not an island: fed from the existing task-completion flow with the
  metrics already persisted on the Task (cost, tokens, duration). No new pipeline,
  no schema change. trace_id == task.id so re-delivered completions upsert.
- Safe by construction: every call is a no-op when Langfuse is not configured,
  and any error is swallowed (debug-logged) so tracing can NEVER break a task.
- Version-resilient: posts directly to the stable Langfuse ingestion REST API
  via httpx (already a dependency) instead of pinning a churning SDK.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from app.config import settings

if TYPE_CHECKING:  # avoid import cycles / runtime cost
    from app.models.task import Task

logger = logging.getLogger(__name__)

_INGESTION_PATH = "/api/public/ingestion"
# Long-form fields are truncated before they ever leave the orchestrator so a
# huge prompt/result can't bloat the trace payload (or leak more than needed).
_MAX_TEXT = 10_000


def _iso(dt: datetime | None) -> str:
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _clip(text: str | None) -> str | None:
    if text is None:
        return None
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "…[truncated]"


class ObservabilityService:
    """Thin, defensive Langfuse ingestion client (singleton via module `observability`)."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def enabled(self) -> bool:
        return settings.langfuse_enabled

    def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=settings.langfuse_host.rstrip("/"),
                auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def record_chat_trace(
        self, db, *, agent_id: str, message_id: str, session_id: str | None = None,
        output: str | None = None, cost_usd: float | None = None,
        input_tokens: int | None = None, output_tokens: int | None = None,
        tool_calls=None, source: str = "chat",
    ) -> None:
        """Emit one trace for a finished chat turn. No-op if disabled.

        Covers the chat path (web/WS + telegram/scheduler) so agent CHAT messages
        are traced too, not just tasks. Groups turns of a conversation via
        sessionId. The user prompt is looked up from the paired user message.
        """
        if not self.enabled or not agent_id or not message_id:
            return
        try:
            from sqlalchemy import select
            from app.models.agent import Agent
            from app.models.chat_message import ChatMessage

            um = await db.scalar(select(ChatMessage).where(
                ChatMessage.agent_id == agent_id,
                ChatMessage.message_id == message_id,
                ChatMessage.role == "user",
            ))
            prompt = um.content if um else None
            user_id = await db.scalar(select(Agent.user_id).where(Agent.id == agent_id))

            now = _iso(None)
            trace_id = f"chat-{message_id}"
            n_tools = len(tool_calls) if isinstance(tool_calls, list) else 0
            trace_body: dict = {
                "id": trace_id,
                "name": "chat",
                "input": _clip(prompt),
                "output": _clip(output),
                "metadata": {"agent_id": agent_id, "source": source, "num_tools": n_tools},
                "tags": ["chat"] + ([f"agent:{agent_id}"] if agent_id else []),
                "timestamp": now,
            }
            if session_id:
                trace_body["sessionId"] = session_id
            if user_id:
                trace_body["userId"] = user_id

            usage = {
                "input": input_tokens or 0,
                "output": output_tokens or 0,
                "total": (input_tokens or 0) + (output_tokens or 0),
                "unit": "TOKENS",
            }
            gen_body: dict = {
                "id": f"gen-chat-{message_id}",
                "traceId": trace_id,
                "type": "GENERATION",
                "name": "chat-response",
                "input": _clip(prompt),
                "output": _clip(output),
                "startTime": now,
                "endTime": now,
                "usage": usage,
                "usageDetails": {"input": usage["input"], "output": usage["output"]},
            }
            if cost_usd is not None:
                gen_body["costDetails"] = {"total": cost_usd}

            batch = {"batch": [
                {"id": str(uuid.uuid4()), "type": "trace-create",
                 "timestamp": now, "body": trace_body},
                {"id": str(uuid.uuid4()), "type": "generation-create",
                 "timestamp": now, "body": gen_body},
            ]}
            resp = await self._http().post(_INGESTION_PATH, json=batch)
            if resp.status_code >= 400:
                logger.debug("Langfuse chat ingestion non-2xx (%s): %s",
                             resp.status_code, resp.text[:300])
        except Exception as exc:
            logger.debug("Langfuse chat trace skipped for %s: %s", message_id, exc)

    async def api_get(self, path: str, params: dict | None = None) -> tuple[int, object]:
        """Proxy a GET to the Langfuse public API. Returns (status_code, json|text).

        Used by the admin-only observability endpoints. Raises RuntimeError when
        Langfuse is not configured so the caller can return a clean 503.
        """
        if not self.enabled:
            raise RuntimeError("observability_disabled")
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = await self._http().get(path, params=clean)
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, resp.text

    def trace_url(self, task_id: str) -> str | None:
        """Browser deep-link into the Langfuse UI for a task's trace (or None)."""
        base = settings.langfuse_public_url.rstrip("/")
        if not base:
            return None
        return f"{base}/project/{settings.langfuse_project_id}/traces/{task_id}"

    async def record_task_trace(self, task: "Task", *, user_id: str | None = None) -> None:
        """Emit one trace (+ one generation) for a finished task. No-op if disabled."""
        if not self.enabled:
            return
        try:
            now = _iso(None)
            start = _iso(task.started_at)
            end = _iso(task.completed_at)
            status_str = getattr(task.status, "value", str(task.status))
            metadata = {
                "agent_id": task.agent_id,
                "status": status_str,
                "num_turns": task.num_turns,
                "duration_ms": task.duration_ms,
                "parent_task_id": task.parent_task_id,
            }
            schedule_id = (task.metadata_ or {}).get("schedule_id")
            if schedule_id:
                metadata["schedule_id"] = schedule_id
            tags = [f"agent:{task.agent_id}"] if task.agent_id else []

            trace_body: dict = {
                "id": task.id,
                "name": task.title or "task",
                "input": _clip(task.prompt),
                "output": _clip(task.result or task.error),
                "metadata": metadata,
                "tags": tags,
                "timestamp": start,
            }
            if user_id:
                trace_body["userId"] = user_id

            usage = {
                "input": task.input_tokens or 0,
                "output": task.output_tokens or 0,
                "total": (task.input_tokens or 0) + (task.output_tokens or 0),
                "unit": "TOKENS",
            }
            gen_body: dict = {
                "id": f"gen-{task.id}",
                "traceId": task.id,
                "type": "GENERATION",
                "name": "llm-generation",
                "model": task.model,
                "input": _clip(task.prompt),
                "output": _clip(task.result),
                "startTime": start,
                "endTime": end,
                "usage": usage,
                "usageDetails": {"input": usage["input"], "output": usage["output"]},
                "level": "ERROR" if status_str == "failed" else "DEFAULT",
            }
            if task.error:
                gen_body["statusMessage"] = _clip(task.error)
            if task.cost_usd is not None:
                gen_body["costDetails"] = {"total": task.cost_usd}

            batch = {
                "batch": [
                    {"id": str(uuid.uuid4()), "type": "trace-create",
                     "timestamp": now, "body": trace_body},
                    {"id": str(uuid.uuid4()), "type": "generation-create",
                     "timestamp": now, "body": gen_body},
                ]
            }
            resp = await self._http().post(_INGESTION_PATH, json=batch)
            if resp.status_code >= 400:
                logger.debug("Langfuse ingestion non-2xx (%s): %s",
                             resp.status_code, resp.text[:300])
        except Exception as exc:  # never let tracing break a task
            logger.debug("Langfuse trace skipped for task %s: %s", task.id, exc)


# Module-level singleton — import this everywhere (one place, verzahnt).
observability = ObservabilityService()
