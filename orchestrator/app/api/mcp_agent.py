"""MCP HTTP Server per Agent.

Exposes each agent as a proper MCP server endpoint that n8n, Cursor, and
other MCP clients can connect to via the 2025-06-18 Streamable HTTP transport.

Endpoint: POST /mcp/agents/{agent_id}
Auth:      Authorization: Bearer <webhook_token>  (same token as webhook)

Supported MCP methods:
  initialize       → server capabilities + agent info
  notifications/initialized → ack (no-op)
  ping             → pong
  tools/list       → available tools for this agent
  tools/call       → execute a tool (send_task, get_status, list_tasks)
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import hmac

from app.db.session import get_db
from app.dependencies import get_redis_service
from app.models.agent import Agent
from app.models.task import Task, TaskStatus
from app.services.redis_service import RedisService

router = APIRouter(prefix="/mcp", tags=["mcp-agent"])

MCP_PROTOCOL_VERSION = "2025-06-18"

AGENT_TOOLS = [
    {
        "name": "send_task",
        "description": "Send a task to this AI agent. The agent will process it autonomously and return a task ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed instructions for the agent. Be specific about what needs to be done.",
                },
                "title": {
                    "type": "string",
                    "description": "Short task title (optional, auto-generated if omitted).",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "get_task_status",
        "description": "Get the current status and result of a previously created task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID returned by send_task.",
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_agent_status",
        "description": "Get the current state of this agent (idle, working, etc.) and its recent activity.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_recent_tasks",
        "description": "List the most recent tasks processed by this agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "string",
                    "description": "Number of tasks to return (default: 10, max: 50).",
                },
            },
        },
    },
    {
        "name": "computer_use",
        "description": (
            "Control the user's desktop via the AI-Employee Bridge app. "
            "First call with action='list_sessions' to find available sessions. "
            "Then call with action='screenshot' to see the screen, or other actions to interact. "
            "The Bridge app must be running and connected on the user's machine."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "Action to perform. Special: 'list_sessions' — lists available bridge sessions. "
                        "Desktop actions: 'screenshot', 'mouse_click', 'mouse_move', 'mouse_scroll', "
                        "'type', 'key', 'hotkey', 'open_app', 'close_app', "
                        "'clipboard_read', 'clipboard_write', 'shell_run', 'ax_tree'."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": "Bridge session ID (from list_sessions). Required for all desktop actions.",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Action parameters. Examples: "
                        "screenshot: {scale: 0.5}; "
                        "mouse_click: {x: 100, y: 200, button: 'left'}; "
                        "type: {text: 'Hello'}; "
                        "key: {key: 'enter'}; "
                        "hotkey: {keys: ['cmd', 'c']}; "
                        "open_app: {name: 'Safari'}; "
                        "shell_run: {command: 'ls -la'}; "
                        "clipboard_write: {text: 'hello'}."
                    ),
                },
            },
            "required": ["action"],
        },
    },
]


def _mcp_result(id_, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _mcp_error(id_, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_result(content: str, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": content}],
        "isError": is_error,
    }


async def _auth_agent(agent_id: str, request: Request, db: AsyncSession) -> Agent:
    """Verify agent exists and Bearer token matches webhook_token."""
    agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if not agent.webhook_enabled:
        raise HTTPException(status_code=403, detail="MCP access is not enabled for this agent. Enable via Settings → Externer Zugriff.")

    if agent.webhook_token:
        auth_header = request.headers.get("Authorization", "")
        provided = auth_header.removeprefix("Bearer ").strip()
        if not provided or not hmac.compare_digest(provided, agent.webhook_token):
            raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")

    return agent


@router.post("/agents/{agent_id}")
async def mcp_agent_endpoint(
    agent_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: RedisService = Depends(get_redis_service),
):
    """MCP Streamable HTTP endpoint — handles all JSON-RPC requests from MCP clients."""
    agent = await _auth_agent(agent_id, request, db)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _mcp_error(None, -32700, "Parse error: invalid JSON"),
            status_code=200,
        )

    # Batch requests (array) — process each and return array
    if isinstance(body, list):
        responses = []
        for req in body:
            resp = await _handle_rpc(req, agent, db, redis)
            if resp is not None:
                responses.append(resp)
        return JSONResponse(responses)

    resp = await _handle_rpc(body, agent, db, redis)
    if resp is None:
        # Notifications have no response
        return JSONResponse(None, status_code=202)
    return JSONResponse(resp)


async def _handle_rpc(req: dict, agent: Agent, db: AsyncSession, redis: RedisService):
    """Dispatch a single JSON-RPC request and return a response dict (or None for notifications)."""
    rpc_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    # --- Notifications (no id, no response) ---
    if rpc_id is None:
        return None

    # --- initialize ---
    if method == "initialize":
        return _mcp_result(rpc_id, {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": f"ai-employee-agent/{agent.name}",
                "version": "1.0.0",
            },
        })

    # --- ping ---
    if method == "ping":
        return _mcp_result(rpc_id, {})

    # --- tools/list ---
    if method == "tools/list":
        return _mcp_result(rpc_id, {"tools": AGENT_TOOLS})

    # --- tools/call ---
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = await _call_tool(tool_name, arguments, agent, db, redis)
        return _mcp_result(rpc_id, result)

    # --- Unknown method ---
    return _mcp_error(rpc_id, -32601, f"Method not found: {method}")


async def _call_tool(name: str, args: dict, agent: Agent, db: AsyncSession, redis: RedisService) -> dict:
    """Execute an agent tool and return an MCP tool result."""

    if name == "send_task":
        prompt = args.get("prompt", "").strip()
        if not prompt:
            return _tool_result("Error: 'prompt' is required", is_error=True)
        title = args.get("title") or prompt[:60]
        task_id = uuid.uuid4().hex[:12]
        # Create DB record so get_task_status can find it
        task_obj = Task(
            id=task_id,
            title=title,
            prompt=prompt,
            status=TaskStatus.QUEUED,
            agent_id=agent.id,
            model=None,
        )
        db.add(task_obj)
        await db.commit()
        payload = json.dumps({
            "id": task_id,
            "prompt": prompt,
            "title": title,
            "model": None,
        })
        await redis.client.lpush(f"agent:{agent.id}:tasks", payload)
        return _tool_result(
            f"Task created successfully.\ntask_id: {task_id}\ntitle: {title}\n\n"
            f"Use get_task_status(task_id='{task_id}') to check progress."
        )

    if name == "get_task_status":
        task_id = args.get("task_id", "").strip()
        if not task_id:
            return _tool_result("Error: 'task_id' is required", is_error=True)
        task = (await db.execute(
            select(Task).where(Task.id == task_id, Task.agent_id == agent.id)
        )).scalar_one_or_none()
        if not task:
            return _tool_result(f"Task '{task_id}' not found.", is_error=True)
        lines = [
            f"task_id: {task.id}",
            f"title: {task.title}",
            f"status: {task.status.value if hasattr(task.status, 'value') else task.status}",
        ]
        if task.result:
            lines.append(f"result: {task.result[:2000]}")
        return _tool_result("\n".join(lines))

    if name == "get_agent_status":
        return _tool_result(
            f"agent_id: {agent.id}\n"
            f"name: {agent.name}\n"
            f"state: {agent.state.value if hasattr(agent.state, 'value') else agent.state}\n"
            f"role: {agent.config.get('role', 'unassigned') if agent.config else 'unassigned'}"
        )

    if name == "list_recent_tasks":
        try:
            limit = min(int(float(str(args.get("limit", 10)))), 50)
        except (ValueError, TypeError):
            limit = 10
        result = await db.execute(
            select(Task)
            .where(Task.agent_id == agent.id)
            .order_by(Task.created_at.desc())
            .limit(limit)
        )
        tasks = result.scalars().all()
        if not tasks:
            return _tool_result("No tasks found for this agent.")
        lines = [f"Recent {len(tasks)} tasks:"]
        for t in tasks:
            status = t.status.value if hasattr(t.status, "value") else t.status
            lines.append(f"  [{t.id}] {t.title} — {status}")
        return _tool_result("\n".join(lines))

    if name == "computer_use":
        from app.api.computer_use import _sessions, _action_allowed, DEFAULT_ALLOWED_CAPABILITIES
        action = args.get("action", "").strip()
        if not action:
            return _tool_result("Error: 'action' is required", is_error=True)

        if action == "list_sessions":
            if not agent.user_id:
                return _tool_result("Error: agent has no associated user", is_error=True)
            user_sessions = [
                {"session_id": sid, "status": "connected" if s["bridge_connected"] else "waiting_for_bridge",
                 "platform": s.get("platform", "unknown"), "agent_id": s.get("agent_id"),
                 "allowed_capabilities": sorted(s.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES))}
                for sid, s in _sessions.items()
                if s["user_id"] == str(agent.user_id)
            ]
            if not user_sessions:
                return _tool_result("No active bridge sessions. Ask the user to open the AI-Employee Bridge app and click 'Verbinden'.")
            lines = [f"Found {len(user_sessions)} bridge session(s):"]
            for s in user_sessions:
                lines.append(f"  session_id={s['session_id']} status={s['status']} platform={s['platform']} assigned_agent={s['agent_id'] or 'any'}")
                lines.append(f"    capabilities: {', '.join(s['allowed_capabilities'])}")
            return _tool_result("\n".join(lines))

        session_id = args.get("session_id", "").strip()
        if not session_id:
            return _tool_result("Error: 'session_id' is required. Call computer_use(action='list_sessions') first.", is_error=True)
        session = _sessions.get(session_id)
        if not session:
            return _tool_result(f"Error: session '{session_id}' not found. It may have expired.", is_error=True)
        if str(session["user_id"]) != str(agent.user_id):
            return _tool_result("Error: session belongs to a different user", is_error=True)
        assigned = session.get("agent_id")
        if assigned and str(assigned) != str(agent.id):
            return _tool_result(f"Error: session is assigned to agent {assigned}, not this agent", is_error=True)
        if not session["bridge_connected"] or not session.get("bridge_ws"):
            return _tool_result("Error: bridge is not connected. Ask the user to open the Bridge app and connect.", is_error=True)
        allowed: set[str] = session.get("allowed_capabilities", DEFAULT_ALLOWED_CAPABILITIES)
        if not _action_allowed(action, allowed):
            return _tool_result(f"Error: action '{action}' is not permitted (capability disabled by user)", is_error=True)

        import asyncio as _asyncio
        cmd_id = uuid.uuid4().hex[:8]
        params = args.get("params", {})
        command_msg = json.dumps({"type": "command", "id": cmd_id, "command": {"action": action, "params": params}})
        session["action_count"] = session.get("action_count", 0) + 1
        session.setdefault("audit_log", []).append({"cmd_id": cmd_id, "action": action, "params": params, "caller": str(agent.id), "ts": __import__("time").time()})
        result_future: _asyncio.Future = _asyncio.get_event_loop().create_future()
        session.setdefault("pending_results", {})[cmd_id] = result_future
        try:
            await session["bridge_ws"].send_text(command_msg)
            result = await _asyncio.wait_for(result_future, timeout=args.get("timeout", 15.0))
            if action == "screenshot":
                b64 = result.get("screenshot_b64", "")
                return {"content": [{"type": "image", "data": b64, "mimeType": "image/png"}]}
            return _tool_result(json.dumps(result, ensure_ascii=False))
        except _asyncio.TimeoutError:
            session["pending_results"].pop(cmd_id, None)
            return _tool_result(f"Error: bridge timed out after {args.get('timeout', 15)}s", is_error=True)
        except Exception as e:
            session["pending_results"].pop(cmd_id, None)
            return _tool_result(f"Error: {e}", is_error=True)

    return _tool_result(f"Unknown tool: {name}", is_error=True)
