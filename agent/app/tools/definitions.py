"""Tool definitions in OpenAI function-calling JSON Schema format.

Contains both local tools (bash, file I/O) and orchestrator API tools
(memory, notifications, tasks, todos, schedules) that replicate the
MCP server functionality for custom LLM agents.
"""

# ── Local Tools (always available) ──

LOCAL_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command in the workspace. Use for running scripts, git, installing packages, builds, tests, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 30, max: 300)",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns the full file content as text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative path to the file",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default: all)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative path to the file",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (default: /workspace)",
                        "default": "/workspace",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, list recursively (max 3 levels deep)",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
]

# ── Orchestrator API Tools (replicate MCP server functionality) ──

ORCHESTRATOR_TOOLS: list[dict] = [
    # ── Task Management (orchestrator-server.mjs) ──
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a task for yourself or another agent. Use to delegate work or schedule follow-up work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The task instructions/prompt",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for the task",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Target agent ID (default: self). Use list_team to find other agents.",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Priority 0-10 (higher = more urgent)",
                        "default": 0,
                    },
                },
                "required": ["prompt", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks in your queue. Filter by status to see pending, running, completed, or failed tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: pending, running, completed, failed (default: all)",
                        "enum": ["pending", "running", "completed", "failed"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_team",
            "description": "List all agents in your team with their names, roles, and current status.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message to another agent for coordination. Use list_team to find agent IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The target agent's ID",
                    },
                    "message": {
                        "type": "string",
                        "description": "The message text to send",
                    },
                },
                "required": ["agent_id", "message"],
            },
        },
    },
    # ── Schedule Management (orchestrator-server.mjs) ──
    {
        "type": "function",
        "function": {
            "name": "create_schedule",
            "description": "Create a recurring schedule that executes a prompt at regular intervals (min 60 seconds).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the schedule",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The prompt to execute each interval",
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": "Interval in seconds (minimum 60)",
                        "default": 3600,
                    },
                },
                "required": ["name", "prompt", "interval_seconds"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_schedules",
            "description": "List all your active and paused schedules.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_schedule",
            "description": "Pause, resume, or delete a schedule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "The schedule ID to manage",
                    },
                    "action": {
                        "type": "string",
                        "description": "Action to take",
                        "enum": ["pause", "resume", "delete"],
                    },
                },
                "required": ["schedule_id", "action"],
            },
        },
    },
    # ── TODO Management (orchestrator-server.mjs) ──
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "List your TODO items. Filter by status or project. TODOs are YOUR assigned work items - check and complete them!",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status",
                        "enum": ["pending", "in_progress", "completed"],
                    },
                    "project": {
                        "type": "string",
                        "description": "Filter by project name",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_todos",
            "description": "Add or replace TODO items in bulk. Previously completed items are preserved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "List of TODO items to add/update",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "default": "pending",
                                },
                                "priority": {
                                    "type": "integer",
                                    "description": "Priority 0-10",
                                    "default": 0,
                                },
                                "project": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    },
                    "project": {
                        "type": "string",
                        "description": "Default project for all TODOs in this batch",
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "Mark a single TODO as completed by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "The TODO ID to mark as completed",
                    },
                },
                "required": ["id"],
            },
        },
    },
    # ── Memory Management (memory-server.mjs) ──
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "Save information to long-term memory. Persists across conversations and restarts. Use for important facts, preferences, and learnings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Memory category",
                        "enum": ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
                    },
                    "key": {
                        "type": "string",
                        "description": "Short identifier/title for this memory",
                    },
                    "content": {
                        "type": "string",
                        "description": "The information to remember",
                    },
                    "importance": {
                        "type": "integer",
                        "description": "Importance 1-5 (higher = returned first in searches)",
                        "default": 3,
                    },
                },
                "required": ["category", "key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search your long-term memories by keyword. Use to recall previously saved information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter",
                        "enum": ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_list",
            "description": "List all your saved memories, optionally filtered by category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Optional category filter",
                        "enum": ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete",
            "description": "Delete a specific memory by its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "The memory ID to delete",
                    },
                },
                "required": ["memory_id"],
            },
        },
    },
    # ── Notifications (notification-server.mjs) ──
    {
        "type": "function",
        "function": {
            "name": "notify_user",
            "description": "Send a notification to the user (appears in Web UI and optionally Telegram). Use for status updates, completions, and alerts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification message body",
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority level (high/urgent also sends Telegram)",
                        "enum": ["low", "normal", "high", "urgent"],
                        "default": "normal",
                    },
                    "type": {
                        "type": "string",
                        "description": "Notification type (affects color/icon)",
                        "enum": ["info", "warning", "error", "success"],
                        "default": "info",
                    },
                },
                "required": ["title", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_approval",
            "description": "Request user approval for an action. The user will see the request on the Approvals page and can approve or deny it. Returns an approval_id that you can later check with check_approval. Use before irreversible or important actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                    "options": {
                        "type": "array",
                        "description": "List of options for the user to choose from",
                        "items": {"type": "string"},
                        "default": ["Yes", "No"],
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context about why approval is needed",
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_approval",
            "description": "Check the status of a previously requested approval. Returns PENDING, APPROVED, or DENIED with the user's reason.",
            "parameters": {
                "type": "object",
                "properties": {
                    "approval_id": {
                        "type": "string",
                        "description": "The approval_id returned by request_approval",
                    },
                },
                "required": ["approval_id"],
            },
        },
    },
]

# ── Combined Tool List ──

# All tools available for custom LLM agents
TOOL_DEFINITIONS: list[dict] = LOCAL_TOOLS + ORCHESTRATOR_TOOLS

# Tool names that are handled by the orchestrator API client (not local execution)
ORCHESTRATOR_TOOL_NAMES: set[str] = {
    t["function"]["name"] for t in ORCHESTRATOR_TOOLS
}
