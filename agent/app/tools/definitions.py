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
                "spreadsheets, ZIPs, audio files, or other deliverables in the workspace. "
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
    # ── Ticketsystem (Matrix42 o.a.) ──
    # Ein Werkzeug mit action-Parameter statt vier; siehe browser/computer_use.
    # Schliessen und Loeschen fehlen bewusst: ein Agent, der ein Ticket eigenmaechtig
    # schliesst, erzeugt genau den Aerger, den die Automatisierung sparen soll.
    {
        "type": "function",
        "function": {
            "name": "tickets",
            "description": (
                "Read and write the company ticket system (Matrix42 or compatible). "
                "Use it to look up an existing ticket, list open ones, file a new ticket, "
                "or add a comment. You can NOT close or delete tickets — a human does that. "
                "Actions: list | get | create | comment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "list | get | create | comment"},
                    "ticket_id": {"type": "string", "description": "For get / comment."},
                    "title": {"type": "string", "description": "For create."},
                    "description": {"type": "string", "description": "For create."},
                    "priority": {"type": "string", "description": "For create (optional)."},
                    "text": {"type": "string", "description": "For comment."},
                    "query": {"type": "string", "description": "For list: system filter expression."},
                    "limit": {"type": "string", "description": "For list: max results (default 20)."},
                },
                "required": ["action"],
            },
        },
    },
    # ── Browser im Container (Codex / Custom-LLM) ──
    # Claude Code bekommt dasselbe ueber den Playwright-MCP; `claude mcp add` schreibt
    # aber in die Konfiguration der Claude-CLI, die diese Laufzeiten nicht lesen. Ohne
    # diesen Eintrag koennte nur einer von drei Harnessen im Browser arbeiten.
    {
        "type": "function",
        "function": {
            "name": "browser",
            "description": (
                "Control a headless browser INSIDE your container: open pages, click, "
                "type, read the rendered text. Use this for public websites and web apps "
                "that need JavaScript — `bash`/`curl` only returns raw HTML, which is "
                "empty for most modern sites. "
                "NOT for the user's own screen or internal company URLs: those go through "
                "`computer_use`, which drives the user's real desktop. "
                "Typical flow: navigate → read_text → click/type → read_text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "navigate | click | type | read_text | read_links | "
                            "screenshot | wait_for | back | close"
                        ),
                    },
                    "url": {"type": "string", "description": "For navigate."},
                    "selector": {
                        "type": "string",
                        "description": "CSS selector. For click/type/wait_for.",
                    },
                    "text": {
                        "type": "string",
                        "description": "For click: match a visible label instead of a selector.",
                    },
                    "value": {"type": "string", "description": "For type: the text to enter."},
                    "submit": {
                        "type": "boolean",
                        "description": "For type: press Enter afterwards.",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "For screenshot: capture the whole page, not just the viewport.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    # ── Computer-Use Desktop Bridge ──
    {
        "type": "function",
        "function": {
            "name": "computer_use",
            "description": (
                "Control the user's real desktop through the AI-Employee Desktop Bridge. "
                "Use this when the user asks to open a URL or app on their computer, "
                "navigate in their browser, click/type on their screen, or take a screenshot "
                "of what they are seeing. Do not use bash, curl, web_search, or an agent-browser "
                "skill for the user's own screen or internal/company URLs. First call "
                "action='list_sessions'; then use the connected session_id for actions. If the "
                "bridge or a capability is unavailable, report that error instead of switching "
                "to a server-side browser."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "Action to perform. Special: 'list_sessions'. Desktop actions: "
                            "'screenshot', 'mouse_click', 'mouse_move', 'mouse_scroll', "
                            "'type', 'key', 'hotkey', 'open_app', 'close_app', "
                            "'clipboard_read', 'clipboard_write', 'shell_run', 'ax_tree', "
                            "'find_element', 'wait_for_element', 'list_windows', "
                            "'focus_window'. Browser in the agent's OWN profile (needs the "
                            "'browser' capability): 'browser_navigate', 'browser_snapshot', "
                            "'browser_click', 'browser_fill', 'browser_wait', "
                            "'browser_capture', 'browser_tabs', 'browser_close'. "
                            "ego lite — the user's REAL, already-logged-in browser session "
                            "(needs the 'ego_browser' capability, off by default; use this "
                            "instead of browser_* when the task needs an account the user is "
                            "already signed into and re-authenticating in a fresh profile "
                            "would be unnecessary friction): 'ego_run' (params: {script: "
                            "'<JS body, same helpers as the ego-browser skill heredoc — "
                            "useOrCreateTaskSpace/openOrReuseTab/snapshotText/click/js/cdp>', "
                            "timeout: 120}; returns whatever the script passed to cliLog())."
                        ),
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Bridge session ID returned by action='list_sessions'. Required for all desktop actions.",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Action parameters. Examples: screenshot {scale: 0.5} "
                            "or screenshot {scale: 0.5, display: 2} for a second monitor "
                            "(the reply states the image size and which displays exist — "
                            "click coordinates must lie inside that size); "
                            "mouse_click {x: 100, y: 200, button: 'left'}; type {text: 'https://example.com'}; "
                            "key {key: 'enter'}; hotkey {keys: ['ctrl', 'l']}; open_app {name: 'Edge'}; "
                            "find_element {query: 'Save', role: 'AXButton'}; "
                            "focus_window {app: 'Excel'}; browser_navigate {url: 'https://…'}; "
                            "browser_fill {selector: '#user', value: 'abc'}; "
                            "browser_click {text: 'Anmelden'}. Prefer find_element over "
                            "guessing coordinates, and browser_* over open_url when you "
                            "actually need to READ or FILL a page."
                        ),
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Timeout in seconds for bridge actions (default 15, max enforced by server/client).",
                    },
                },
                "required": ["action"],
            },
        },
    },
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
            "description": (
                "SYSTEM-WIDE directory of agents visible to you across teams, with names, "
                "roles and status. Use it to FIND someone outside your own team. "
                "This is NOT your team: when asked 'which agents do you have / who is on "
                "your team', answer from list_my_team ALONE and never merge these entries "
                "into it — an agent from another team is not your colleague, and naming "
                "them as one leads to work handed to someone who never picks it up."
            ),
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
            "name": "list_agent_messages",
            "description": "List recent inter-agent messages involving you. Use this when the user asks whether another agent contacted you, replied to you, or sent you a message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {
                        "type": "integer",
                        "description": "How far back to look in minutes (default: 240)",
                        "default": 240,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_conversation",
            "description": "Read your conversation history with another agent. Use list_team to find the agent ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The other agent's ID",
                    },
                },
                "required": ["agent_id"],
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
            "name": "complete_onboarding",
            "description": (
                "Finish YOUR onboarding: record who you are and what you are permanently responsible for. Call this as soon as the user has answered what your role is and which recurring duties you take over — do NOT keep asking afterwards. Every duty you pass becomes a Verantwortungsbereich, and from the next proactive run you build your own day from them instead of waiting for someone to file a todo. At least one duty is required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "Your role in one sentence, e.g. 'Sekretariat der IT-Leitung'."},
                    "responsibilities": {
                        "type": "array",
                        "description": "Every RECURRING duty the user named. At least one.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Short and concrete, e.g. 'Posteingang sichten'."},
                                "rhythm": {"type": "string", "description": "daily | weekly | monthly | continuous"},
                                "priority": {"type": "string", "description": "high | normal | low"},
                                "notes": {"type": "string", "description": "How you know today's pass is done."},
                            },
                            "required": ["title"],
                        },
                    },
                    "boundaries": {"type": "string", "description": "What you must NOT do."},
                    "notes": {"type": "string", "description": "Other standing instructions from the user."},
                },
                "required": ["responsibilities"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_day",
            "description": (
                "Write down what you intend to do TODAY, so it becomes visible to the user "
                "in the agent calendar instead of living only in your notes. Call this at the "
                "START of a proactive run, after you worked out your plan (STEP 1): pass the "
                "blocks in the order you mean to work them. Replaces the plan you wrote "
                "earlier for that day — items already running or done are kept. The user can "
                "move or drop a block; read it back with get_day_plan on your next run and "
                "respect their changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The blocks you plan for the day, in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "What you will do — short and concrete."},
                                "notes": {"type": "string", "description": "Optional detail (how you know it's done)."},
                                "planned_start": {"type": "string", "description": "ISO-8601 UTC start, e.g. '2026-08-07T07:30:00Z'. Omit if the order matters but the clock doesn't."},
                                "estimated_minutes": {"type": "integer", "description": "Rough duration in minutes (default 30)."},
                                "source": {"type": "string", "description": "'responsibility' (from a standing duty), 'todo' (existing todo), 'self' (your own idea)."},
                                "priority": {"type": "string", "description": "high | normal | low — inherit it from the responsibility or todo this block works on."},
                                "todo_id": {"type": "integer", "description": "Link to the todo this block works on, if any."},
                            },
                            "required": ["title"],
                        },
                    },
                    "plan_date": {"type": "string", "description": "Day as YYYY-MM-DD. Default: today (UTC)."},
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_day_plan",
            "description": (
                "Read the day plan — yours by default. Use it at the start of a run to see "
                "what you planned earlier AND what the user changed (they can move or drop "
                "blocks; a dropped block must not be worked on)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Day as YYYY-MM-DD. Default: today."},
                    "days": {"type": "integer", "description": "How many days from 'date' (default 1)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_schedule",
            "description": (
                "Schedule YOURSELF to run a prompt later — you pick the timing. "
                "run_in_seconds = ONE-SHOT self follow-up ('look at this again in 30 min' → 1800), fires once then stops. "
                "interval_seconds = repeat forever every N seconds. "
                "cron_expression = exact wall-clock times (e.g. daily/twice-daily). "
                "Give exactly ONE of the three. Use this instead of sleeping/waiting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the schedule",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The prompt to execute when it fires",
                    },
                    "run_in_seconds": {
                        "type": "integer",
                        "description": "ONE-SHOT: run once after this many seconds (min 30), then auto-disable. E.g. 1800 = in 30 minutes.",
                    },
                    "interval_seconds": {
                        "type": "integer",
                        "description": "RECURRING: repeat every N seconds (minimum 60). Prefer cron_expression for exact times.",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "RECURRING at exact times IN YOUR OWN TIMEZONE, e.g. '0 6 * * *' = every day 06:00, '0 9,17 * * *' = 09:00 and 17:00.",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone for cron_expression. LEAVE EMPTY unless you mean a different zone than your own — the server uses yours. Setting 'UTC' by hand is how a schedule named '(07:00)' ends up firing at 09:00.",
                    },
                },
                "required": ["name", "prompt"],
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
    # ── Event Trigger Management (orchestrator-server.mjs) ──
    {
        "type": "function",
        "function": {
            "name": "trigger_create",
            "description": (
                "Set yourself up to react to an EVENT instead of polling on a timer — fires "
                "a task for you when a matching webhook arrives (e.g. a GitHub PR, a Stripe "
                "payment, any inbound webhook your setup receives). Use this instead of "
                "create_schedule when the work is event-driven, not time-driven."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the trigger"},
                    "prompt_template": {
                        "type": "string",
                        "description": (
                            "The prompt to run when the trigger fires. Supports "
                            "{{payload.field}} interpolation from the webhook payload."
                        ),
                    },
                    "source_filter": {
                        "type": "string",
                        "description": "Only fire for webhooks from this source, e.g. 'github', 'stripe'. Omit to match any source.",
                    },
                    "event_type_filter": {
                        "type": "string",
                        "description": "Only fire for this event type, e.g. 'pull_request', 'payment'. Omit to match any type.",
                    },
                    "payload_conditions": {
                        "type": "object",
                        "description": "Field:value pairs that must match in the webhook payload, e.g. {\"action\": \"opened\"}. Omit for no extra conditions.",
                    },
                    "priority": {
                        "type": "integer",
                        "description": "Task priority when the trigger fires (default 5)",
                    },
                },
                "required": ["name", "prompt_template"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_list",
            "description": "List all your event triggers.",
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
            "name": "trigger_toggle",
            "description": "Enable or disable one of your event triggers (does not delete it).",
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_id": {"type": "string", "description": "The trigger ID to toggle"},
                },
                "required": ["trigger_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_delete",
            "description": "Delete one of your event triggers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_id": {"type": "string", "description": "The trigger ID to delete"},
                },
                "required": ["trigger_id"],
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
                    "is_checkin": {
                        "type": "boolean",
                        "description": (
                            "Set true ONLY for a proactive 'nothing left to do, checking in with "
                            "a suggestion' notification (PROACTIVE_PROMPT STEP 3). Server-enforced "
                            "to at most once per 12h per agent — extra check-ins in the same window "
                            "are silently dropped. Do NOT set this for real accomplishments, "
                            "results, or actionable problems; those are never rate-limited."
                        ),
                        "default": False,
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
            "description": "Request user approval and WAIT for the decision. This call BLOCKS until the user approves or denies (the agent pauses here) and returns the decision directly, including the chosen option — you do NOT need to poll with check_approval. If APPROVED, proceed; if DENIED or no decision, STOP and do not perform the action. Use before irreversible or important actions.",
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
            "name": "present_view",
            "description": (
                "Ask the user something with a PICTURE instead of a list of words, "
                "and WAIT for the answer. Blocks exactly like request_approval and "
                "returns what the user chose.\n\n"
                "Use it when the answer is easier to point at than to describe — "
                "choosing between images you generated is the clear case. If plain "
                "words do the job, use request_approval; a view for a yes/no "
                "question is just slower.\n\n"
                "The view itself lives in the web UI; you pick one by name and hand "
                "it data. Views available today:\n"
                "  image_choice — several images side by side, the user picks one. "
                "Data: {\"images\": [{\"path\": \"/workspace/...\", \"label\": \"...\"}]}. "
                "Give FILE PATHS in your workspace, never image content.\n\n"
                "Always pass `options` as well: they are the same question in plain "
                "words. Telegram, the phone app and voice-only use them — a view "
                "that only works in one place leaves those users stuck."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "description": "Which view to show",
                        "enum": ["image_choice"],
                    },
                    "data": {
                        "type": "object",
                        "description": "The view's payload — see the description for the shape it expects",
                    },
                    "question": {
                        "type": "string",
                        "description": "The question, in words. Shown above the view and used wherever the view cannot be drawn.",
                    },
                    "options": {
                        "type": "array",
                        "description": (
                            "The same choices in plain words — the fallback for "
                            "Telegram, phone and voice. Keep them in the same order "
                            "as the view's items."
                        ),
                        "items": {"type": "string"},
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context for the user",
                    },
                },
                "required": ["view", "data", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_if_unsure",
            "description": (
                "Report how confident you are (0-100) BEFORE acting on an uncertain "
                "decision. The SERVER decides whether that is enough: at or above the "
                "operator's threshold this returns immediately and costs nothing — "
                "nobody is bothered. Only below the threshold is the decision handed to "
                "a human, and then this BLOCKS until they answer. "
                "Use it whenever you would otherwise GUESS: ambiguous instructions, "
                "several plausible readings, missing information you cannot look up, an "
                "irreversible step you are unsure about. A guessed result is worse than a "
                "question — it looks like work and is not. "
                "For actions that are risky but CLEAR, use request_approval instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confidence": {
                        "type": "number",
                        "description": (
                            "How sure you are, 0-100 (0.0-1.0 also accepted). Be honest — "
                            "inflating this defeats the entire mechanism."
                        ),
                    },
                    "question": {
                        "type": "string",
                        "description": (
                            "What you would ask the human. State the actual decision, "
                            "not 'is this ok?'."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "Why you are unsure and what the options are — everything "
                            "the human needs to decide without asking you back."
                        ),
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The concrete choices, if there are distinct ones.",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "The task this decision belongs to, if any.",
                    },
                },
                "required": ["confidence", "question"],
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
            "name": "secondbrain_search",
            "description": "Search the SHARED Second Brain VAULT(s) — the department's Markdown knowledge base mounted under /mnt/brains/<slug>/, shared by MANY users (what users browse in the UI under Wissen → Second Brain). Use this for support/how-to/troubleshooting (error codes, devices, procedures) BEFORE answering. NOT the personal Knowledge Base (that is brain_search).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords (error codes, device names, topics)."},
                    "limit": {"type": "number", "description": "Max files (default 10, max 50)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "secondbrain_read",
            "description": "Read the full content of one file in the shared Second Brain vault, by path (as returned by secondbrain_search/secondbrain_list, e.g. 'it_operations/Drucker/x17137.md').",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Vault-relative path under /mnt/brains."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "secondbrain_write",
            "description": "Create or overwrite a Markdown file in the SHARED Second Brain vault (department knowledge the whole team sees). USE THIS to 'write into the Second Brain / vault' — NOT brain_contribute. Path is vault-relative, e.g. 'it_operations/Drucker/HP-Fax.md'. Only works if the brain is mounted read-write. Use sensible folders/filenames, [[wikilinks]] between topics, and plain-text error codes/model names so search finds them.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Vault-relative path incl. brain slug, e.g. 'it_operations/Drucker/HP-Fax.md'."},
                    "content": {"type": "string", "description": "Full Markdown content of the article."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "secondbrain_list",
            "description": "List the mounted Second Brain vault(s) and their files (with read-only/read-write status), so you can pick what to read or where to write.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brain_search",
            "description": "Semantic search across THIS USER'S personal KNOWLEDGE BASE (account-bound; the 'Knowledge' tab; shared across this user's own agents). This is NOT the shared Second Brain vault (department .md files under /mnt/brains — use secondbrain_search for that). Returns entries ranked by similarity. Call before tasks to load personal context and before creating entries to avoid duplicates.",
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
            "description": "Add/update an entry in THIS USER'S personal KNOWLEDGE BASE (account-bound; the 'Knowledge' tab; upsert by title). Use for the user's own research/decisions/insights. This is NOT the shared Second Brain vault — to write shared department knowledge (.md the whole team sees), use secondbrain_write instead. Auto-links related entries.",
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
    # ── Team-Werkzeuge (Paritaet mit orchestrator-server.mjs) ──
    # Fehlten bis 2026-08-12 im Custom-LLM. Ohne sie kann ein Agent weder
    # sehen, wer zu ihm gehoert, noch was die anderen tun — und erfindet es.
    {
        "type": "function",
        "function": {
            "name": "list_my_team",
            "description": "Who is on my team — the agents I can delegate to. Call this BEFORE claiming what other agents are doing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_team_tasks",
            "description": "What my team is actually working on right now. Use this instead of guessing or describing a status you have not checked.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks_status",
            "description": "Check whether tasks I delegated are still running or finished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "Task ids to check."
                    }
                },
                "required": [
                    "task_ids"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_meeting",
            "description": "Set up a meeting between several agents to align on a topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "What the meeting is about."
                    },
                    "agent_ids": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "Participating agents."
                    }
                },
                "required": [
                    "topic"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "skill_update",
            "description": "Update one of my installed skills.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "The skill to update."
                    },
                    "content": {
                        "type": "string",
                        "description": "New skill content."
                    }
                },
                "required": [
                    "skill_id"
                ]
            }
        }
    },
    # ── Delegieren und auf das Ergebnis warten ──
    # Gab es bis 2026-08-12 NUR im MCP-Satz, also nur fuer Claude Code. Ohne dieses
    # Werkzeug beschreibt ein Modell die Delegation, statt sie auszufuehren — beim
    # Kunden stand eine erfundene Statustabelle im Chat, waehrend alle Agenten
    # nachweislich im Leerlauf waren.
    {
        "type": "function",
        "function": {
            "name": "delegate_and_wait",
            "description": (
                "Give concrete work to OTHER agents and WAIT for their results. Use this "
                "whenever you say you will 'beauftragen', 'delegieren', 'aufteilen' or "
                "report on what other agents are doing — announcing it without calling "
                "this tool is a false statement. Creates real tasks on their boards and "
                "returns each result (or says plainly that one is still running). "
                "Max 20 tasks, wait up to 600s. For fire-and-forget use create_task_batch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "The pieces of work, one per agent.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Short title."},
                                "prompt": {
                                    "type": "string",
                                    "description": (
                                        "Full instruction. Must stand on its own: the "
                                        "receiver has its OWN /workspace and cannot see "
                                        "yours. Never point at a /workspace/... path of "
                                        "yours — copy what they need to /shared/ and name "
                                        "that path, or put the content into this prompt."
                                    ),
                                },
                                "agent_id": {"type": "string", "description": "Target agent id (omit = auto-assign)."},
                                "priority": {"type": "integer", "description": "1 (high) to 10 (low), default 5."},
                            },
                            "required": ["title", "prompt"],
                        },
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "How long to wait for results (10-600, default 300).",
                    },
                },
                "required": ["tasks"],
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
    # ── App Management (my own docker-compose apps; orchestrator runs them) ──
    {
        "type": "function",
        "function": {
            "name": "list_apps",
            "description": "List MY OWN docker-compose apps (the projects under /workspace/projects/) with running status and containers. I have NO docker myself — the platform (orchestrator) runs them; use the app_* tools to drive them. Use the app 'path' from here for the other app tools.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "app_logs",
            "description": "Read the container logs of one of MY apps (to debug why it won't start or misbehaves). Pass the app 'path' from list_apps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "App path in /workspace (from list_apps)."},
                    "service": {"type": "string", "description": "Optional: only this compose service."},
                    "lines": {"type": "integer", "description": "Log lines to fetch (10-1000, default 100)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_app",
            "description": "Start one of MY apps via the orchestrator (docker compose up -d --build). Use to bring a stopped or newly-created app up. Pass the app 'path' from list_apps.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "App path in /workspace (from list_apps)."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_app",
            "description": "Stop one of MY apps (docker compose down). Pass the app 'path' from list_apps.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "App path in /workspace (from list_apps)."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rebuild_app",
            "description": "Rebuild one of MY apps from its CURRENT code and restart it (docker compose up -d --build --force-recreate). ALWAYS use this after I changed an app's code/config in the workspace — a plain start of an already-built app does NOT pick up my changes. Pass the app 'path' from list_apps.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "App path in /workspace (from list_apps)."}},
                "required": ["path"],
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
