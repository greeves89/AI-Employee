"""Orchestrator API client - direct HTTP calls to replicate MCP server functionality.

Replaces the 4 MCP servers (orchestrator, memory, notifications, bash-approval)
with direct API calls for custom_llm agents.
"""

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class OrchestratorAPIClient:
    """HTTP client for orchestrator API - same endpoints used by MCP servers."""

    def __init__(self):
        self.base_url = settings.orchestrator_url.rstrip("/") + "/api/v1"
        self.agent_id = settings.agent_id
        self.agent_name = settings.agent_name or settings.agent_id
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.agent_token}",
                "X-Agent-ID": self.agent_id,
            },
        )

    async def _request(
        self, method: str, path: str, json: dict | None = None, params: dict | None = None
    ) -> dict | list | str:
        """Make an API request and return parsed response."""
        url = f"{self.base_url}{path}"
        try:
            resp = await self._client.request(method, url, json=json, params=params)
            if resp.status_code >= 400:
                return f"API error {resp.status_code}: {resp.text[:500]}"
            return resp.json()
        except httpx.ConnectError:
            return "Error: Cannot connect to orchestrator API"
        except Exception as e:
            return f"Error: {e}"

    # ── Task Management (orchestrator-server.mjs) ──

    async def create_task(self, params: dict) -> str:
        """Create a task for self or another agent."""
        agent_id = params.get("agent_id", self.agent_id)
        body = {
            "title": params.get("title", "Task from agent"),
            "prompt": params.get("prompt", ""),
            "priority": params.get("priority", 0),
            "agent_id": agent_id,
            "model": params.get("model"),
        }
        result = await self._request("POST", "/tasks/", json=body)
        if isinstance(result, str):
            return result
        return f"Task created: id={result.get('id')}, agent={agent_id}"

    async def list_tasks(self, params: dict) -> str:
        """List tasks for this agent."""
        query: dict[str, Any] = {"agent_id": self.agent_id}
        if params.get("status"):
            query["status"] = params["status"]
        result = await self._request("GET", "/tasks/", params=query)
        if isinstance(result, str):
            return result
        tasks = result.get("tasks", [])
        if not tasks:
            return "No tasks found."
        lines = []
        for t in tasks:
            lines.append(f"- [{t.get('status', '?')}] {t.get('title', 'Untitled')} (id: {t.get('id')})")
        return "\n".join(lines)

    async def list_team(self, params: dict) -> str:
        """List all agents in the team."""
        result = await self._request("GET", "/agents/team/directory")
        if isinstance(result, str):
            return result
        agents = result.get("agents", [])
        if not agents:
            return "No team members found."
        lines = []
        for a in agents:
            status = a.get("status", "unknown")
            lines.append(f"- {a.get('name', '?')} (id: {a.get('id')}, role: {a.get('role', 'none')}, status: {status})")
        return "\n".join(lines)

    async def send_message(self, params: dict) -> str:
        """Send a message to another agent."""
        target_id = params.get("agent_id", "")
        if not target_id:
            return "Error: agent_id is required"
        body = {
            "from_agent_id": self.agent_id,
            "from_name": self.agent_name,
            "text": params.get("message", ""),
        }
        result = await self._request("POST", f"/agents/{target_id}/message", json=body)
        if isinstance(result, str):
            return result
        return f"Message sent to agent {target_id}"

    # ── Schedule Management (orchestrator-server.mjs) ──

    async def create_schedule(self, params: dict) -> str:
        """Create a recurring schedule."""
        body = {
            "name": params.get("name", "Agent Schedule"),
            "prompt": params.get("prompt", ""),
            "interval_seconds": max(params.get("interval_seconds", 3600), 60),
            "agent_id": self.agent_id,
            "model": params.get("model"),
        }
        result = await self._request("POST", "/schedules/", json=body)
        if isinstance(result, str):
            return result
        return f"Schedule created: {result.get('name')} (id: {result.get('id')}, interval: {result.get('interval_seconds')}s)"

    async def list_schedules(self, params: dict) -> str:
        """List all schedules for this agent."""
        result = await self._request("GET", "/schedules/")
        if isinstance(result, str):
            return result
        schedules = result.get("schedules", [])
        if not schedules:
            return "No schedules found."
        lines = []
        for s in schedules:
            active = "active" if s.get("active") else "paused"
            lines.append(f"- {s.get('name', '?')} ({active}, every {s.get('interval_seconds', '?')}s, id: {s.get('id')})")
        return "\n".join(lines)

    async def manage_schedule(self, params: dict) -> str:
        """Manage a schedule (pause/resume/delete)."""
        schedule_id = params.get("schedule_id", "")
        action = params.get("action", "")
        if not schedule_id or not action:
            return "Error: schedule_id and action are required"

        if action == "delete":
            result = await self._request("DELETE", f"/schedules/{schedule_id}")
        elif action in ("pause", "resume"):
            result = await self._request("POST", f"/schedules/{schedule_id}/{action}")
        else:
            return f"Error: Unknown action '{action}'. Use pause, resume, or delete."

        if isinstance(result, str):
            return result
        return f"Schedule {schedule_id} {action}d successfully"

    # ── TODO Management (orchestrator-server.mjs) ──

    async def list_todos(self, params: dict) -> str:
        """List TODOs for this agent."""
        query: dict[str, Any] = {}
        if params.get("status"):
            query["status"] = params["status"]
        if params.get("project"):
            query["project"] = params["project"]
        result = await self._request("GET", "/todos/agent/list", params=query)
        if isinstance(result, str):
            return result
        todos = result.get("todos", [])
        summary = f"Total: {result.get('total', 0)} | Pending: {result.get('pending', 0)} | In Progress: {result.get('in_progress', 0)} | Completed: {result.get('completed', 0)}"
        if not todos:
            return f"No TODOs found.\n{summary}"
        lines = [summary, ""]
        for t in todos:
            priority = f" [P{t.get('priority', 0)}]" if t.get("priority") else ""
            project = f" ({t.get('project')})" if t.get("project") else ""
            lines.append(f"- [{t.get('status', '?')}]{priority}{project} {t.get('title', 'Untitled')} (id: {t.get('id')})")
        return "\n".join(lines)

    async def update_todos(self, params: dict) -> str:
        """Bulk add/replace TODOs."""
        body: dict[str, Any] = {
            "todos": params.get("todos", []),
        }
        if params.get("task_id"):
            body["task_id"] = params["task_id"]
        if params.get("project"):
            body["project"] = params["project"]
        if params.get("project_path"):
            body["project_path"] = params["project_path"]
        result = await self._request("PUT", "/todos/agent/bulk", json=body)
        if isinstance(result, str):
            return result
        return f"TODOs updated: {result.get('added', 0)} added, {result.get('updated', 0)} updated, {result.get('total', 0)} total"

    async def complete_todo(self, params: dict) -> str:
        """Mark a single TODO as completed."""
        todo_id = params.get("id", "")
        if not todo_id:
            return "Error: id is required"
        result = await self._request("PATCH", f"/todos/agent/{todo_id}/complete")
        if isinstance(result, str):
            return result
        return f"TODO completed: {result.get('title', todo_id)}"

    # ── Memory Management (memory-server.mjs) ──

    async def memory_save(self, params: dict) -> str:
        """Save a memory.

        Supports the issue #24 upgrade fields:
          - room:     hierarchical path for retrieval grouping
          - tag_type: 'transient' (fast decay) | 'permanent' (slow decay)
          - tags:     canonical tags (auto-normalized server-side)
          - override: confirm supersede on 409 contradiction warning
          - confidence: 1.0 = observed, 0.5 = inferred, 1.5 = user-corrected
        """
        body: dict[str, Any] = {
            "agent_id": self.agent_id,
            "category": params.get("category", "fact"),
            "key": params.get("key", ""),
            "content": params.get("content", ""),
            "importance": params.get("importance", 3),
        }
        # Only include upgrade fields when explicitly provided so we stay
        # backwards-compatible with older-shape tool calls.
        if params.get("room"):
            body["room"] = params["room"]
        if params.get("tag_type") in ("transient", "permanent"):
            body["tag_type"] = params["tag_type"]
        if params.get("tags"):
            body["tags"] = params["tags"]
        if params.get("override") is True:
            body["override"] = True
        if "confidence" in params:
            body["confidence"] = params["confidence"]

        result = await self._request("POST", "/memory/save", json=body)
        if isinstance(result, str):
            # _request returns a string on HTTP errors. A 409 contradiction
            # will come back here as "Error 409: ..." — surface it cleanly.
            if "409" in result or "contradiction" in result.lower():
                return (
                    f"⚠️ Contradiction warning: a similar memory already exists. "
                    f"Review it with memory_search, then re-call memory_save with "
                    f"override=true if you want to replace it.\n\nDetail: {result}"
                )
            return result

        extras = []
        if result.get("room"):
            extras.append(f"room={result['room']}")
        if result.get("tag_type") and result["tag_type"] != "permanent":
            extras.append(f"tag_type={result['tag_type']}")
        extras_str = f" ({', '.join(extras)})" if extras else ""
        return (
            f"Memory saved: [{result.get('category')}] "
            f"{result.get('key')} (id: {result.get('id')}){extras_str}"
        )

    async def memory_search(self, params: dict) -> str:
        """Search memories.

        For best precision, pass `room` to narrow retrieval to a specific
        project/area. The server applies multi-strategy re-ranking
        (semantic + structural + recency + importance).
        """
        query: dict[str, Any] = {
            "agent_id": self.agent_id,
            "q": params.get("query", ""),
        }
        if params.get("category"):
            query["category"] = params["category"]
        if params.get("room"):
            query["room"] = params["room"]

        # Prefer semantic-search when available — it's the upgraded path.
        endpoint = "/memory/semantic-search" if params.get("query") else "/memory/search"
        result = await self._request("GET", endpoint, params=query)
        if isinstance(result, str):
            # Fall back to the plain search endpoint if semantic-search fails
            result = await self._request("GET", "/memory/search", params=query)
            if isinstance(result, str):
                return result
        memories = result.get("memories", [])
        if not memories:
            return "No memories found."
        header = f"Found {len(memories)} memories"
        if result.get("mode") == "semantic_reranked":
            header += " (re-ranked: semantic + room + recency + importance)"
        lines = [header, ""]
        for m in memories:
            score = m.get("score")
            score_str = f" (score={score:.3f})" if score is not None else ""
            room_str = f" [{m.get('room')}]" if m.get("room") else ""
            lines.append(
                f"- [{m.get('category')}] {m.get('key')}{room_str}{score_str}: "
                f"{m.get('content', '')[:200]}"
            )
        return "\n".join(lines)

    async def memory_list(self, params: dict) -> str:
        """List all memories."""
        query: dict[str, Any] = {}
        if params.get("category"):
            query["category"] = params["category"]
        result = await self._request("GET", f"/memory/agents/{self.agent_id}", params=query)
        if isinstance(result, str):
            return result
        memories = result.get("memories", [])
        if not memories:
            return "No memories stored."
        lines = [f"Total: {result.get('total', 0)} | Categories: {', '.join(result.get('categories', []))}", ""]
        for m in memories:
            lines.append(f"- [{m.get('category')}] {m.get('key')} (importance: {m.get('importance', 0)}, id: {m.get('id')})")
        return "\n".join(lines)

    async def memory_delete(self, params: dict) -> str:
        """Delete a memory."""
        memory_id = params.get("memory_id", "")
        if not memory_id:
            return "Error: memory_id is required"
        result = await self._request("DELETE", f"/memory/{memory_id}")
        if isinstance(result, str):
            return result
        return f"Memory {memory_id} deleted"

    # ── Notifications (notification-server.mjs) ──

    async def notify_user(self, params: dict) -> str:
        """Send a notification to the user."""
        body = {
            "agent_id": self.agent_id,
            "type": params.get("type", "info"),
            "title": params.get("title", "Agent Notification"),
            "message": params.get("message", ""),
            "priority": params.get("priority", "normal"),
        }
        result = await self._request("POST", "/notifications/", json=body)
        if isinstance(result, str):
            return result
        return f"Notification sent (priority: {result.get('priority', 'normal')})"

    async def request_approval(self, params: dict) -> str:
        """Request user approval for an action via the approvals system."""
        body = {
            "question": params.get("question", ""),
            "options": params.get("options", ["Yes", "No"]),
            "context": params.get("context", ""),
            "risk_level": "medium",
        }
        result = await self._request("POST", "/approvals/request", json=body)
        if isinstance(result, str):
            return result
        approval_id = result.get("approval_id", "unknown")
        return (
            f"Approval requested (id: {approval_id}). "
            f"The user will see this on the Approvals page. "
            f"Use check_approval(approval_id='{approval_id}') to check the user's decision."
        )

    async def check_approval(self, params: dict) -> str:
        """Check the status of a previously requested approval."""
        approval_id = params.get("approval_id", "")
        if not approval_id:
            return "Error: approval_id is required"
        result = await self._request("GET", f"/approvals/check/{approval_id}")
        if isinstance(result, str):
            return result
        status = result.get("status", "unknown")
        if status == "pending":
            return f"Approval {approval_id}: PENDING - User has not decided yet."
        elif status == "approved":
            return f"Approval {approval_id}: APPROVED by user at {result.get('approved_at', 'unknown')}."
        elif status == "denied":
            reason = result.get("deny_reason", "No reason given")
            return f"Approval {approval_id}: DENIED. Reason: {reason}"
        return f"Approval {approval_id}: Status is '{status}'."

    # ── Knowledge Base (knowledge-server.mjs parity) ──

    async def knowledge_write(self, params: dict) -> str:
        """Write to the shared knowledge base (upsert by title)."""
        body = {
            "title": params.get("title", ""),
            "content": params.get("content", ""),
            "tags": params.get("tags", []),
        }
        result = await self._request("POST", "/knowledge/agent/write", json=body)
        if isinstance(result, str):
            return result
        backlinks = result.get("backlinks", [])
        bl = f", links to: [{', '.join(backlinks)}]" if backlinks else ""
        return f"Knowledge saved: \"{result.get('title')}\" (id: {result.get('id')}, tags: [{', '.join(result.get('tags', []))}]{bl})"

    async def knowledge_search(self, params: dict) -> str:
        """Search the shared knowledge base (semantic + keyword fallback)."""
        query: dict[str, Any] = {}
        if params.get("query"):
            query["q"] = params["query"]
        if params.get("tag"):
            query["tag"] = params["tag"]
        result = await self._request("GET", "/knowledge/agent/search", params=query)
        if isinstance(result, str):
            return result
        entries = result.get("entries", [])
        if not entries:
            return f"No knowledge entries found for \"{params.get('query', '')}\"."
        mode = result.get("mode", "keyword")
        lines = [f"Found {result.get('total', 0)} entries via {mode}:", ""]
        for e in entries:
            sim = f" [{e['similarity']*100:.0f}% match]" if e.get("similarity") else ""
            lines.append(f"**{e['title']}**{sim} (tags: [{', '.join(e.get('tags', []))}])")
            lines.append(e.get("content", "")[:300])
            lines.append("---")
        return "\n".join(lines)

    async def knowledge_read(self, params: dict) -> str:
        """Read a specific knowledge entry by title."""
        title = params.get("title", "")
        if not title:
            return "Error: title is required"
        result = await self._request("GET", f"/knowledge/agent/read/{title}")
        if isinstance(result, str):
            return result
        return f"# {result.get('title')}\nTags: [{', '.join(result.get('tags', []))}]\n\n{result.get('content', '')}"

    # ── Batch Tasks ──

    async def create_task_batch(self, params: dict) -> str:
        """Create multiple tasks in parallel."""
        tasks = params.get("tasks", [])
        if not tasks:
            return "Error: tasks list is required"
        body = {
            "tasks": [
                {
                    "title": t.get("title", "Task"),
                    "prompt": t.get("prompt", ""),
                    "priority": t.get("priority", 5),
                    "agent_id": t.get("agent_id"),
                }
                for t in tasks
            ],
            "created_by_agent": self.agent_id,
        }
        result = await self._request("POST", "/tasks/batch", json=body)
        if isinstance(result, str):
            return result
        created = result.get("tasks", [])
        lines = [f"Batch created: {len(created)} tasks running in parallel:"]
        for t in created:
            lines.append(f"  - #{t.get('id')}: \"{t.get('title')}\" → {t.get('agent_id', 'auto')} [{t.get('status')}]")
        return "\n".join(lines)

    # ── Synchronous messaging ──

    async def send_message_and_wait(self, params: dict) -> str:
        """Send a message and wait for reply (up to 45s)."""
        target_id = params.get("agent_id", "")
        if not target_id:
            return "Error: agent_id is required"

        # Get current max message ID
        before = await self._request("GET", "/agents/team/poll-reply", params={
            "from_agent_id": target_id, "to_agent_id": self.agent_id, "since_id": "0", "timeout": "1",
        })
        since_id = before.get("message", {}).get("id", 0) if isinstance(before, dict) and before.get("message") else 0

        # Send the message
        body = {
            "from_agent_id": self.agent_id,
            "from_name": self.agent_name,
            "text": params.get("message", ""),
            "message_type": params.get("message_type", "question"),
        }
        await self._request("POST", f"/agents/{target_id}/message", json=body)

        # Poll for reply
        poll = await self._request("GET", "/agents/team/poll-reply", params={
            "from_agent_id": target_id, "to_agent_id": self.agent_id, "since_id": str(since_id), "timeout": "45",
        })
        if isinstance(poll, dict) and poll.get("found") and poll.get("message"):
            msg = poll["message"]
            return f"Reply from {msg.get('from_name', target_id)}:\n\n{msg.get('text', '')}"
        return f"Message sent to {target_id}, but no reply within 45s. The reply will arrive later."

    # ── Telegram ──

    async def send_voice(self, params: dict) -> str:
        """Convert text to speech via VibeVoice and send as Telegram voice message."""
        import base64
        text = params.get("text", "").strip()
        language = params.get("language", "de")
        if not text:
            return "Error: text is required"

        # 1. TTS — call local VibeVoice service
        tts_url = settings.tts_service_url if hasattr(settings, "tts_service_url") else "http://host.docker.internal:8002"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(
                    f"{tts_url}/synthesize",
                    json={"text": text, "language": language},
                )
                resp.raise_for_status()
                audio_bytes = resp.content
                provider = resp.headers.get("x-tts-provider", "tts")
        except Exception as e:
            return f"TTS service unavailable: {e}. Is tts-service running? (./tts-service/start_mac.sh)"

        # 2. Send audio via orchestrator → Telegram
        voice_b64 = base64.b64encode(audio_bytes).decode()
        result = await self._request(
            "POST", "/telegram/send-voice",
            json={"voice_base64": voice_b64},
        )
        if isinstance(result, str):
            return result
        return f"Voice message sent via {provider} ({len(audio_bytes):,} bytes)"

    async def send_telegram(self, params: dict) -> str:
        """Send a message or file to the user via Telegram."""
        import json as _json
        message = params.get("message", "")
        file_path = params.get("file_path")

        # Send text via Redis pub/sub (same as notification system)
        import redis.asyncio as aioredis
        try:
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            payload = {"text": message, "agent_id": self.agent_id}
            if file_path:
                payload["file_path"] = file_path
            await r.publish("telegram:send", _json.dumps(payload))
            await r.aclose()
            return f"Telegram message sent" + (f" with file: {file_path}" if file_path else "")
        except Exception as e:
            return f"Error sending Telegram message: {e}"

    # ── Skill Marketplace ──

    async def create_skill(self, params: dict) -> str:
        """Save a reusable skill to the marketplace."""
        body = {
            "name": params.get("title", "untitled-skill").lower().replace(" ", "-"),
            "description": params.get("description", ""),
            "content": params.get("solution", ""),
            "category": params.get("category", "pattern"),
        }
        result = await self._request("POST", "/skills/agent/propose", json=body)
        if isinstance(result, str):
            return result
        return (
            f"Skill '{params.get('title')}' saved to marketplace "
            f"(id: {result.get('id', '')}, status: draft — pending user review)"
        )

    async def rate_task(self, params: dict) -> str:
        """Rate own task performance and optionally ask for user feedback."""
        rating = max(1, min(5, int(params.get("rating", 3))))
        reflection = params.get("reflection", "")
        ask_feedback = params.get("ask_feedback", True)

        # Record self-rating via dedicated endpoint
        result = await self._request(
            "POST",
            "/ratings/task-self-rate",
            json={"rating": rating, "reflection": reflection},
        )

        stars = "⭐" * rating
        msg = f"Task self-rated {stars} ({rating}/5): {reflection}"
        if isinstance(result, dict) and result.get("id"):
            msg += f" (rating id: {result['id']})"

        if ask_feedback:
            msg += (
                "\n\n💬 **Feedback gewünscht:** War das hilfreich? "
                "Was kann ich beim nächsten Mal besser machen?"
            )
        return msg

    async def skill_search(self, params: dict) -> str:
        """Search the skill marketplace."""
        query: dict[str, Any] = {}
        if params.get("query"):
            query["q"] = params["query"]
        if params.get("category"):
            query["category"] = params["category"]
        result = await self._request("GET", "/skills/agent/search", params=query)
        if isinstance(result, str):
            return result
        skills = result.get("skills", [])
        if not skills:
            return "No skills found. Consider proposing one with skill_propose."
        lines = [f"Found {len(skills)} skills:", ""]
        for s in skills:
            rating = f" [{'★' * round(s.get('avg_rating', 0))}{'☆' * (5 - round(s.get('avg_rating', 0)))}]" if s.get("avg_rating") else ""
            lines.append(f"**{s.get('name')}** (id={s.get('id')} | {s.get('category')}){rating} — {s.get('description', '')}")
            lines.append(s.get("content", "")[:200])
            lines.append(f"→ Use skill_install(skill_id={s.get('id')}) to install this skill.")
            lines.append("---")
        return "\n".join(lines)

    async def skill_propose(self, params: dict) -> str:
        """Propose a new skill (draft, needs user review)."""
        body = {
            "name": params.get("name", ""),
            "description": params.get("description", ""),
            "content": params.get("content", ""),
            "category": params.get("category", "pattern"),
        }
        result = await self._request("POST", "/skills/agent/propose", json=body)
        if isinstance(result, str):
            return result
        return f"Skill proposed: \"{result.get('name')}\" (id: {result.get('id')}, status: draft). User will be notified."

    async def skill_get_my_skills(self, params: dict) -> str:
        """Get all skills assigned to this agent."""
        import os
        # Always scan filesystem first — guaranteed source of truth
        skills_dir = os.path.join(os.environ.get("WORKSPACE_DIR", "/workspace"), ".claude", "skills")
        fs_skills: list[dict] = []
        if os.path.isdir(skills_dir):
            for entry in os.listdir(skills_dir):
                skill_md = os.path.join(skills_dir, entry, "SKILL.md")
                if os.path.isfile(skill_md):
                    try:
                        with open(skill_md) as f:
                            content = f.read()
                        fs_skills.append({"name": entry, "description": content.split("\n")[0].lstrip("# "), "content": content[:300]})
                    except Exception:
                        pass

        # Also try DB-assigned skills
        result = await self._request("GET", "/skills/agent/available")
        db_skills: list[dict] = []
        if not isinstance(result, str):
            db_skills = result.get("skills", [])

        # Merge: DB skills first, then filesystem (by name, no duplicates)
        db_names = {s.get("name") for s in db_skills}
        all_skills = db_skills + [s for s in fs_skills if s["name"] not in db_names]

        if not all_skills:
            return "No skills found. Search with skill_search or check /workspace/.claude/skills/ manually."
        lines = [f"You have {len(all_skills)} skills:", ""]
        for s in all_skills:
            lines.append(f"**{s.get('name')}** — {s.get('description', '')}")
            lines.append(s.get("content", "")[:300])
            lines.append("---")
        return "\n".join(lines)

    async def skill_install(self, params: dict) -> str:
        """Install a marketplace skill to this agent."""
        skill_id = params.get("skill_id")
        if not skill_id:
            return "Error: skill_id is required"
        result = await self._request("POST", f"/skills/agent/install/{skill_id}")
        if isinstance(result, dict) and result.get("status") in ("installed", "already_installed"):
            content = result.get("content", "")
            name = result.get("skill_name", str(skill_id))
            status = result.get("status")
            msg = f"Skill '{name}' {'installed' if status == 'installed' else 'already installed'}."
            if content:
                msg += f"\n\nSkill instructions:\n{content}"
            return msg
        return f"Error installing skill: {result}"

    async def skill_rate(self, params: dict) -> str:
        """Record skill usage and rating after using a marketplace skill."""
        body = {
            "skill_id": params.get("skill_id"),
            "task_id": params.get("task_id"),
            "helpfulness": params.get("helpfulness"),
            "rating": params.get("rating"),
            "comment": params.get("comment", ""),
        }
        if not body["skill_id"]:
            return "Error: skill_id is required"
        result = await self._request("POST", "/skills/agent/record-usage", json=body)
        if isinstance(result, dict) and result.get("status") == "recorded":
            return (
                f"Skill usage recorded. "
                f"Avg rating: {result.get('avg_rating', 'n/a')}, "
                f"Total uses: {result.get('usage_count', 'n/a')}"
            )
        return f"Error recording skill usage: {result}"

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
