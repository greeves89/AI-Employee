"""Tool definitions in OpenAI function-calling JSON Schema format.

Contains both local tools (bash, file I/O) and orchestrator API tools
(memory, notifications, tasks, todos, schedules) that replicate the
MCP server functionality for custom LLM agents.

Skill tools are loaded dynamically from the skills/ marketplace directory
via app.skills_loader and merged into LOCAL_TOOLS at module load time.
"""

from app.skills_loader import get_skill_tool_definitions, load_all_skills

# Auto-discover and register all skills on import
load_all_skills()

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
            "name": "view_image",
            "description": (
                "Look at an image so you can SEE and analyze its visual content "
                "(photos, screenshots, charts, diagrams, scanned documents). The "
                "image is shown directly to you — no OCR or shell tricks needed. "
                "Provide exactly one of: path (an image file in the workspace), "
                "file_id (a Telegram file_id from the message header), or url."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to an image file (jpg/png/gif/webp), absolute or workspace-relative",
                    },
                    "file_id": {
                        "type": "string",
                        "description": "A Telegram file_id — the image is fetched and shown to you directly",
                    },
                    "url": {
                        "type": "string",
                        "description": "A http(s) URL pointing to an image",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "present_image",
            "description": (
                "Show an image FILE to the user — a chart, diagram, or picture you "
                "generated (e.g. with matplotlib or Pillow) or processed. The image "
                "is rendered inline in the chat UI. Set send_telegram=true to also "
                "deliver it as a Telegram photo to the user. Generate the file "
                "first (write it into the workspace with code), then call this with "
                "its path. Supported: png, jpg, gif, webp; max 5 MB."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the image file to present, absolute or workspace-relative",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional short caption shown with the image",
                    },
                    "send_telegram": {
                        "type": "boolean",
                        "description": "If true, also send the image to the user via Telegram (default false)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "present_file",
            "description": (
                "Show a generated or prepared FILE to the user as a downloadable "
                "attachment in the chat UI. Use this after creating PDFs, DOCX, "
                "spreadsheets, ZIPs, or other deliverables in the workspace. "
                "Generate the file first, then call this with its path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file, absolute or workspace-relative",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional short caption shown with the attachment",
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
            "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. If the file ALREADY exists you must read_file it first — overwriting a file you haven't read is rejected.",
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
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Perform an exact string replacement in a file. You MUST read_file the file first — editing a file you haven't read is rejected. STRONGLY PREFER THIS over write_file for modifying existing files — it's token-efficient and safe. The old_string must match EXACTLY (including whitespace/indentation) and appear exactly once in the file (unless replace_all=true). Include enough surrounding context in old_string to make it unique.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative path to the file",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to find and replace (must be unique in the file)",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The text to replace it with",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence of old_string (default: false)",
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_edit",
            "description": "Apply multiple edits to a single file atomically. You MUST read_file the file first — editing a file you haven't read is rejected. All edits succeed or all fail. Each edit is applied sequentially to the result of the previous one. Use when you need several related changes in one file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or workspace-relative path to the file",
                    },
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_string": {"type": "string"},
                                "new_string": {"type": "string"},
                                "replace_all": {"type": "boolean", "default": False},
                            },
                            "required": ["old_string", "new_string"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Fast regex search across files using ripgrep. Returns matching lines with file paths and line numbers. Use this INSTEAD of bash(grep/find). Supports glob filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: workspace)",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob filter e.g. '*.py', '**/*.ts' (optional)",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Case-insensitive match (default: false)",
                        "default": False,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matches to return (default: 100)",
                        "default": 100,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern, sorted by modification time (newest first). Use INSTEAD of bash(find). Patterns like '**/*.py' match recursively.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g. '**/*.ts', 'src/**/*.py')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Root directory to search (default: workspace)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Use this when you need current data (weather, news, prices, facts) or don't know which URL to visit. Returns top search results with titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'weather Berlin today', 'Python FastAPI tutorial', 'latest AI news')",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5, max: 10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return its content as text/markdown. Use for reading documentation, API specs, GitHub README files, etc. HTML is stripped to readable text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch (http:// or https://)",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters to return (default: 20000)",
                        "default": 20000,
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show the git working tree status (modified/staged/untracked files). Much cleaner than bash(git status).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository path (default: workspace)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff. By default shows unstaged changes. Set staged=true for staged, or provide a ref (e.g. 'HEAD~1') to diff against a commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Repository path (default: workspace)",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "Show staged changes instead of unstaged",
                        "default": False,
                    },
                    "ref": {
                        "type": "string",
                        "description": "Compare against this ref (e.g. 'HEAD~1', 'main')",
                    },
                    "file": {
                        "type": "string",
                        "description": "Limit diff to this file path",
                    },
                },
                "required": [],
            },
        },
    },
]

# Merge marketplace skill tools into LOCAL_TOOLS
LOCAL_TOOLS.extend(get_skill_tool_definitions())

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
                    "parent_task_id": {
                        "type": "string",
                        "description": "Link this as a subtask of a parent task. The parent agent will be notified when this subtask completes.",
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
            "description": (
                "Save information to long-term memory. Persists across conversations and restarts.\n\n"
                "IMPORTANT — use room + tag_type for good retrieval later:\n"
                "  • room:     hierarchical path like 'project:ai-employee/backend/auth'.\n"
                "              Same project+area → same room.\n"
                "  • tag_type: 'transient' for current task state (decays in ~30d),\n"
                "              'permanent'  for learned patterns and decisions (long-lived).\n\n"
                "If the system returns a 409 contradiction warning, it means a very similar\n"
                "memory already exists. Review it and re-call with override=true to replace it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Memory category (broad bucket)",
                        "enum": ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "Short identifier/title. Preferred canonical keys: "
                            "current_goal, current_task (single-value — new replaces old); "
                            "code_pattern, approach_used, lesson_learned, touched_file, "
                            "referenced_url (multi-value — coexist)."
                        ),
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
                    "room": {
                        "type": "string",
                        "description": (
                            "Hierarchical room path, e.g. 'project:ai-employee/backend/auth'. "
                            "Use a consistent prefix per project so retrieval can filter by area. "
                            "Leave empty only for truly cross-project memories."
                        ),
                    },
                    "tag_type": {
                        "type": "string",
                        "enum": ["transient", "permanent"],
                        "description": (
                            "'transient' = short-lived task state (current todo, recent error, "
                            "work-in-progress). Decays within ~30 days.  "
                            "'permanent' = learned patterns, architecture decisions, user "
                            "preferences. Decays very slowly. Default: permanent."
                        ),
                        "default": "permanent",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Canonical tags for the memory. Choose from: task, code, decision, "
                            "learning, error, correction, pattern, architecture, performance, "
                            "security, user_preference, meta."
                        ),
                    },
                    "override": {
                        "type": "boolean",
                        "description": (
                            "Only set to true after you got a 409 contradiction warning AND you "
                            "confirmed the new content should replace the existing one. The "
                            "old memory is kept as an audit trail via superseded_by."
                        ),
                        "default": False,
                    },
                    "confidence": {
                        "type": "number",
                        "description": (
                            "1.0 = directly observed/confirmed, 0.5 = inferred from context, "
                            "1.5 = user-corrected (never auto-decay)."
                        ),
                        "default": 1.0,
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
            "description": (
                "Search long-term memories with semantic re-ranking.\n\n"
                "IMPORTANT: always pass `room` if you know which project/area you're working "
                "in — it dramatically improves precision (33% fewer irrelevant hits). "
                "Superseded memories are automatically excluded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language query. Semantic search, not keyword.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter",
                        "enum": ["preference", "contact", "project", "procedure", "decision", "fact", "learning"],
                    },
                    "room": {
                        "type": "string",
                        "description": (
                            "Hierarchical room filter. Exact matches get 1.0 structural score, "
                            "sub-rooms 0.7, parent-rooms 0.5, cousins 0.3. Leave empty to search "
                            "across all rooms."
                        ),
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
            "description": "Send a notification to the user. Set target_channel to the channel the user is currently using (webapp, ios, telegram) unless they asked otherwise.",
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
                    "target_channel": {
                        "type": "string",
                        "description": "Preferred delivery channel for this user notification",
                        "enum": ["webapp", "ios", "telegram", "all"],
                        "default": "webapp",
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
            "description": "Request user approval for an action or decision. The user sees it in Approvals and gets a channel-aware notification. Returns an approval_id that you can check with check_approval. Use before irreversible or important actions.",
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
                    "target_channel": {
                        "type": "string",
                        "description": "Preferred delivery channel for the approval prompt",
                        "enum": ["webapp", "ios", "telegram", "all"],
                        "default": "webapp",
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
    # ── Second Brain (full CRUD over the user's unified knowledge graph) ──
    {
        "type": "function",
        "function": {
            "name": "brain_search",
            "description": "Semantic search across the user's Second Brain — the unified knowledge graph shared by ALL agents of this user. Returns entries ranked by similarity. Call this BEFORE starting any task to load context and BEFORE creating new entries to avoid duplicates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "Search query — what you're looking for."},
                    "limit": {"type": "number", "description": "Max results (default 10, max 50)."},
                    "include_memories": {"type": "boolean", "description": "Also search agent memories across user's agents (default false)."},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_contribute",
            "description": "Add or update a node in the user's Second Brain (upsert by title). Use to share research, decisions, insights, processes with all other agents. Auto-links to semantically related entries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Unique title (used as upsert key). Use [[Other Title]] in content for explicit links."},
                    "content": {"type": "string", "description": "Markdown content."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags (e.g. ['decision', 'research'])."},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_get",
            "description": "Fetch the full content of a single brain entry by id. Use after brain_search to read full content.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "number", "description": "Entry id from brain_search/brain_list."}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_list",
            "description": "Paginated list of brain entries (titles + tags only). Use to browse what's in the brain without fetching full content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "number", "description": "Max entries (default 50, max 200)."},
                    "offset": {"type": "number", "description": "Pagination offset (default 0)."},
                    "tag": {"type": "string", "description": "Optional tag filter."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_update",
            "description": "Update an existing brain entry by id. Re-embeds and re-links. Use for fixes/refinements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "number", "description": "Entry id to update."},
                    "title": {"type": "string", "description": "New title (optional)."},
                    "content": {"type": "string", "description": "New content (optional)."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "New tags (optional)."},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_delete",
            "description": "Delete a brain entry by id. Also removes its semantic links. Irreversible.",
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "number", "description": "Entry id to delete."}},
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_related",
            "description": "Get semantically related entries for a given node (cosine similarity). Use for discovery: 'what else is connected to this?'",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "number", "description": "Entry id to find neighbors for."},
                    "limit": {"type": "number", "description": "Max related entries (default 10, max 50)."},
                },
                "required": ["id"],
            },
        },
    },
    # ── Batch Tasks (orchestrator-server.mjs parity) ──
    {
        "type": "function",
        "function": {
            "name": "create_task_batch",
            "description": "Create multiple tasks in parallel for different agents. All run simultaneously. Use to split complex work: e.g. research + code + test on 3 agents at once. Max 20 tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "prompt": {"type": "string"},
                                "priority": {"type": "number", "minimum": 1, "maximum": 10},
                                "agent_id": {"type": "string"},
                            },
                            "required": ["title", "prompt"],
                        },
                    },
                },
                "required": ["tasks"],
            },
        },
    },
    # ── Synchronous messaging (orchestrator-server.mjs parity) ──
    {
        "type": "function",
        "function": {
            "name": "send_message_and_wait",
            "description": "Send a message to another agent AND wait for their reply (up to 45s). Use when you need the answer in the current conversation. If the target agent is busy with a task, the message is queued and the tool returns immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Agent to message"},
                    "message": {"type": "string", "description": "The message to send"},
                    "message_type": {"type": "string", "enum": ["question", "message"], "description": "Default: question"},
                },
                "required": ["agent_id", "message"],
            },
        },
    },
    # ── Telegram (notification-server.mjs parity) ──
    {
        "type": "function",
        "function": {
            "name": "send_voice",
            "description": "Send a voice message to the user via Telegram. Converts text to speech using VibeVoice (free, local AI). Use for summaries, completed task announcements, or when the user prefers audio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to convert to speech and send as voice message"},
                    "language": {"type": "string", "description": "Language code: de, en, fr, es, it, ... (default: de)"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": "Send a message or file to the user via Telegram. Use for notifications, status updates, or delivering files (PDFs, images, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Text message to send"},
                    "file_path": {"type": "string", "description": "Optional: path to a file to send as attachment"},
                },
                "required": ["message"],
            },
        },
    },
    # ── Skill Marketplace — agent-facing create & rate ──
    {
        "type": "function",
        "function": {
            "name": "create_skill",
            "description": "Save a reusable skill/solution to the marketplace after completing a task. Call this when you've built something that could be reused in future tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short skill name"},
                    "description": {"type": "string", "description": "What this skill does (1-2 sentences)"},
                    "solution": {"type": "string", "description": "The actual approach, code, or prompt used"},
                    "category": {"type": "string", "description": "Category: web, data, communication, coding, research, other"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Keywords"},
                },
                "required": ["title", "description", "solution"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rate_task",
            "description": "Rate your own task performance after completion. Always call this at the end of every task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "rating": {"type": "integer", "description": "1-5 stars"},
                    "reflection": {"type": "string", "description": "One sentence: what went well or could be improved"},
                    "ask_feedback": {"type": "boolean", "description": "Whether to ask the user for feedback (default true)"},
                },
                "required": ["rating", "reflection"],
            },
        },
    },
    # ── Skill Marketplace (skill-server.mjs parity) ──
    {
        "type": "function",
        "function": {
            "name": "skill_search",
            "description": "Search the skill marketplace for reusable routines, templates, workflows, patterns. Use before inventing your own solution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "category": {"type": "string", "enum": ["routine", "template", "workflow", "pattern", "recipe", "tool"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_propose",
            "description": "Propose a new skill for the marketplace. Submitted as draft for user review. Use when you discover a reusable pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "lowercase-hyphenated name"},
                    "description": {"type": "string", "description": "One-line description"},
                    "content": {"type": "string", "description": "Full instructions in markdown"},
                    "category": {"type": "string", "enum": ["routine", "template", "workflow", "pattern", "recipe", "tool"]},
                },
                "required": ["name", "description", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_get_my_skills",
            "description": "Get all skills assigned to you. Check at start of complex tasks for relevant skills.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_install",
            "description": "Install a skill from the marketplace to yourself. Call after skill_search when you find a relevant skill. The skill content is returned immediately so you can use it right away.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "integer", "description": "ID of the skill to install (from skill_search results)"},
                },
                "required": ["skill_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_rate",
            "description": "Record that you used a skill and rate how helpful it was. MANDATORY after using a marketplace skill. Also call this when the user gives feedback on your result — pass user_rating based on their sentiment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "integer", "description": "ID of the skill you used"},
                    "task_id": {"type": "string", "description": "Current task ID (CURRENT_TASK_ID)"},
                    "helpfulness": {"type": "integer", "description": "How helpful was the skill? 1=not helpful, 5=essential", "minimum": 1, "maximum": 5},
                    "rating": {"type": "integer", "description": "Your overall self-rating of task quality. 1-5.", "minimum": 1, "maximum": 5},
                    "user_rating": {"type": "integer", "description": "User feedback rating 1-5. Interpret from natural language: 'super/perfekt'=5, 'gut/ok'=4, 'geht so'=3, 'nicht gut'=2, 'schlecht'=1. Only set when user has actually given feedback.", "minimum": 1, "maximum": 5},
                    "comment": {"type": "string", "description": "What worked well or what could be improved in the skill"},
                },
                "required": ["skill_id", "helpfulness", "rating"],
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
