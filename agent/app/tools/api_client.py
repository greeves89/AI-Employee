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
        """Save a memory."""
        body = {
            "agent_id": self.agent_id,
            "category": params.get("category", "fact"),
            "key": params.get("key", ""),
            "content": params.get("content", ""),
            "importance": params.get("importance", 3),
        }
        result = await self._request("POST", "/memory/save", json=body)
        if isinstance(result, str):
            return result
        return f"Memory saved: [{result.get('category')}] {result.get('key')} (id: {result.get('id')})"

    async def memory_search(self, params: dict) -> str:
        """Search memories."""
        query: dict[str, Any] = {
            "agent_id": self.agent_id,
            "q": params.get("query", ""),
        }
        if params.get("category"):
            query["category"] = params["category"]
        result = await self._request("GET", "/memory/search", params=query)
        if isinstance(result, str):
            return result
        memories = result.get("memories", [])
        if not memories:
            return "No memories found."
        lines = [f"Found {result.get('total', 0)} memories:", ""]
        for m in memories:
            lines.append(f"- [{m.get('category')}] {m.get('key')}: {m.get('content', '')[:200]}")
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
        """Request user approval for an action."""
        options = params.get("options", ["Yes", "No"])
        body = {
            "agent_id": self.agent_id,
            "type": "approval",
            "title": "Approval Required",
            "message": params.get("question", ""),
            "priority": "high",
            "meta": {
                "options": options,
                "context": params.get("context", ""),
            },
        }
        result = await self._request("POST", "/notifications/", json=body)
        if isinstance(result, str):
            return result
        return f"Approval requested (id: {result.get('id')}). Waiting for user response."

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
