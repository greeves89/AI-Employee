"""Tool executor - runs tools locally or via orchestrator API."""

import asyncio
import glob as _glob
import hashlib
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from app.config import settings
from app.skills_loader import execute_skill_tool, get_skill_tool_names
from app.tools.definitions import ORCHESTRATOR_TOOL_NAMES

logger = logging.getLogger(__name__)

# Safety limit for bash output
MAX_OUTPUT_CHARS = 30000

# Maps tool name → autonomy category (must match DB category strings exactly)
# Tools NOT listed here are always allowed (read-only / meta tools).
TOOL_CATEGORY_MAP: dict[str, str] = {
    # Shell execution (matches L3 DB category "shell_exec")
    "bash": "shell_exec",
    # File writes (matches DB category "file_write")
    "write_file": "file_write",
    "edit_file": "file_write",
    "multi_edit": "file_write",
    # External communication
    "send_telegram": "external_communication",
    "notify_user": "external_communication",
    # Shared knowledge writes
    "knowledge_write": "knowledge_write",
    # Package installation
    "install_package": "system_config",
}

# These are ALWAYS allowed regardless of whitelist
ALWAYS_ALLOWED_TOOLS = frozenset({
    "request_approval",
    "rate_task",
    "memory_save", "memory_search", "memory_list", "memory_delete",
    "read_file", "list_files", "glob", "grep",
    "git_status", "git_diff",
    "web_search", "web_fetch",
    "knowledge_search", "knowledge_read",
    "list_team", "list_tasks", "list_todos", "list_schedules",
    "skill_search", "skill_get_my_skills",
    "send_message", "create_task",
})


def _get_allowed_categories() -> set[str] | None:
    """Fetch allowed tool categories from the orchestrator whitelist.

    Returns None if no approval rules are configured (= allow everything).
    Cached with 60s TTL so level changes propagate without restart.
    """
    now = time.time()
    cached = _get_allowed_categories._cache
    if cached and now < cached[1]:
        return cached[0]

    try:
        import urllib.request as _req
        import json as _json
        url = f"{settings.orchestrator_url}/api/v1/approval-rules/for-agent/{settings.agent_id}"
        req = _req.Request(url, headers={"X-Agent-Token": settings.agent_token})
        with _req.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read())
        rules = data.get("rules", [])
        if not rules:
            # No rules = no restrictions
            categories = None
        else:
            # If any rule explicitly covers ALL actions (L4 wildcard), treat as unrestricted
            names = {r.get("name", "").lower() for r in rules}
            if any(w in names for w in ("alles erlaubt", "all allowed", "full autonomy")):
                categories = None
            else:
                categories = {r["category"] for r in rules}
        _get_allowed_categories._cache = (categories, now + _AUTONOMY_CACHE_TTL)
        return categories
    except Exception:
        return None  # Fail open — don't block on orchestrator unavailability


_get_allowed_categories._cache = (None, 0.0)

# Tools safe to execute concurrently (read-only, no side effects)
CONCURRENT_SAFE_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep",
    "git_status", "git_diff",
    "web_search", "web_fetch",
    "memory_search", "knowledge_search", "list_team",
    "list_tasks", "list_todos", "list_schedules",
})

# Subset of concurrent-safe tools whose results are worth caching
_CACHEABLE_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep",
    "memory_search", "knowledge_search", "list_team",
    "list_tasks", "list_todos", "list_schedules",
})
_CACHE_TTL_SECONDS = 120  # 2 minutes (tool result cache)
_AUTONOMY_CACHE_TTL = 10  # 10s — fast enough for level changes to propagate


class _ToolCache:
    """In-memory TTL cache for read-only tool results.

    Avoids re-running identical tool calls within a short window.
    Only caches tools that are safe to cache (no side effects).
    """

    def __init__(self, ttl: int = _CACHE_TTL_SECONDS):
        self._store: dict[str, tuple[str, float]] = {}  # key → (result, expires_at)
        self._ttl = ttl
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(tool_name: str, tool_input: dict) -> str:
        raw = f"{tool_name}:{json.dumps(tool_input, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def get(self, tool_name: str, tool_input: dict) -> str | None:
        if tool_name not in _CACHEABLE_TOOLS:
            return None
        key = self._key(tool_name, tool_input)
        entry = self._store.get(key)
        if entry and entry[1] > time.time():
            self.hits += 1
            return entry[0]
        if entry:
            del self._store[key]  # Expired
        self.misses += 1
        return None

    def put(self, tool_name: str, tool_input: dict, result: str) -> None:
        if tool_name not in _CACHEABLE_TOOLS:
            return
        key = self._key(tool_name, tool_input)
        self._store[key] = (result, time.time() + self._ttl)
        # Evict expired entries periodically (every 50 puts)
        if len(self._store) > 200:
            now = time.time()
            self._store = {k: v for k, v in self._store.items() if v[1] > now}

    def invalidate(self) -> None:
        """Clear cache after write operations (edit_file, write_file, bash, etc)."""
        if self._store:
            self._store.clear()


class ToolExecutor:
    """Executes tool calls locally or routes them to the orchestrator API."""

    def __init__(self, workspace_dir: str | None = None):
        self.workspace_dir = workspace_dir or settings.workspace_dir
        self._api_client = None  # Lazy init
        self._mcp_client = None  # Lazy init for custom MCP servers
        self._cache = _ToolCache()
        self._semaphore = asyncio.Semaphore(settings.tool_max_concurrency)

    def _get_api_client(self):
        """Lazy-initialize the orchestrator API client."""
        if self._api_client is None:
            from app.tools.api_client import OrchestratorAPIClient
            self._api_client = OrchestratorAPIClient()
        return self._api_client

    def _get_mcp_client(self):
        """Lazy-initialize the MCP HTTP client."""
        if self._mcp_client is None:
            from app.tools.mcp_client import MCPHTTPClient
            self._mcp_client = MCPHTTPClient()
        return self._mcp_client

    async def execute(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool and return the result as a string.

        Concurrent-safe tools are guarded by a semaphore (TOOL_MAX_CONCURRENCY).
        Read-only tools are cached for 2 minutes to avoid redundant work.
        Write operations invalidate the cache automatically.
        Autonomy whitelist is enforced here at code level — blocked tools return
        an error instructing the agent to call request_approval first.
        """
        # Hard autonomy enforcement — independent of LLM compliance
        if tool_name not in ALWAYS_ALLOWED_TOOLS:
            category = TOOL_CATEGORY_MAP.get(tool_name)
            if category is not None:
                allowed = _get_allowed_categories()
                if allowed is not None and category not in allowed:
                    return (
                        f"[AUTONOMY BLOCK] Tool '{tool_name}' requires category "
                        f"'{category}' which is NOT in your current whitelist. "
                        f"You MUST call `request_approval` first and wait for "
                        f"user approval before attempting this action."
                    )

        if tool_name in CONCURRENT_SAFE_TOOLS:
            async with self._semaphore:
                return await self._execute_inner(tool_name, tool_input)
        return await self._execute_inner(tool_name, tool_input)

    async def _execute_inner(self, tool_name: str, tool_input: dict) -> str:
        # Check cache for read-only tools
        cached = self._cache.get(tool_name, tool_input)
        if cached is not None:
            return cached

        # Route orchestrator API tools to the API client
        if tool_name in ORCHESTRATOR_TOOL_NAMES:
            result = await self._execute_api_tool(tool_name, tool_input)
            self._cache.put(tool_name, tool_input, result)
            return result

        # Route custom MCP tools (prefixed with "mcp_")
        if tool_name.startswith("mcp_"):
            return await self._execute_mcp_tool(tool_name, tool_input)

        # Route marketplace skill tools
        if tool_name in get_skill_tool_names():
            return await execute_skill_tool(tool_name, tool_input)

        # Local tool execution
        handler = getattr(self, f"_tool_{tool_name}", None)
        if not handler:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            result = await handler(tool_input)
            # Cache read-only results; invalidate cache on write operations
            if tool_name in _CACHEABLE_TOOLS:
                self._cache.put(tool_name, tool_input, result)
            elif tool_name in ("write_file", "edit_file", "multi_edit", "bash"):
                self._cache.invalidate()
            return result
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    async def _execute_api_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool via the orchestrator API client."""
        client = self._get_api_client()
        handler = getattr(client, tool_name, None)
        if not handler:
            return f"Error: API tool '{tool_name}' not implemented"
        try:
            return await handler(tool_input)
        except Exception as e:
            return f"Error executing API tool {tool_name}: {e}"

    async def _execute_mcp_tool(self, tool_name: str, tool_input: dict) -> str:
        """Execute a tool via custom MCP HTTP server."""
        client = self._get_mcp_client()
        # Strip "mcp_" prefix and find the server + original tool name
        try:
            return await client.call_tool(tool_name, tool_input)
        except Exception as e:
            return f"Error executing MCP tool {tool_name}: {e}"

    def _resolve_path(self, path: str) -> str:
        """Resolve a path relative to workspace if not absolute."""
        if os.path.isabs(path):
            return path
        return os.path.join(self.workspace_dir, path)

    async def _tool_bash(self, params: dict) -> str:
        """Execute a bash command."""
        command = params.get("command", "")
        if not command:
            return "Error: No command provided"

        timeout = min(params.get("timeout", 30), 300)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_dir,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            output = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            result = ""
            if output:
                result += output
            if err:
                result += f"\n[stderr]\n{err}" if result else err
            if not result:
                result = f"(exit code: {process.returncode})"

            if len(result) > MAX_OUTPUT_CHARS:
                result = result[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(result)} total chars)"

            return result

        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return f"Error: Command timed out after {timeout}s"

    async def _tool_read_file(self, params: dict) -> str:
        """Read a file's contents."""
        path = self._resolve_path(params.get("path", ""))
        max_lines = params.get("max_lines")

        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        if not os.path.isfile(path):
            return f"Error: Not a file: {path}"

        try:
            with open(path, "r", errors="replace") as f:
                if max_lines:
                    lines = []
                    for i, line in enumerate(f):
                        if i >= max_lines:
                            lines.append(f"... (truncated at {max_lines} lines)")
                            break
                        lines.append(line)
                    content = "".join(lines)
                else:
                    content = f.read()

            if len(content) > MAX_OUTPUT_CHARS:
                content = content[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(content)} total chars)"
            return content
        except Exception as e:
            return f"Error reading file: {e}"

    async def _tool_write_file(self, params: dict) -> str:
        """Write content to a file."""
        path = self._resolve_path(params.get("path", ""))
        content = params.get("content", "")

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} chars to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    async def _tool_list_files(self, params: dict) -> str:
        """List directory contents."""
        path = self._resolve_path(params.get("path", self.workspace_dir))
        recursive = params.get("recursive", False)

        if not os.path.exists(path):
            return f"Error: Path not found: {path}"
        if not os.path.isdir(path):
            return f"Error: Not a directory: {path}"

        try:
            entries = []
            if recursive:
                for root, dirs, files in os.walk(path):
                    # Limit depth to 3 levels
                    depth = root[len(path):].count(os.sep)
                    if depth >= 3:
                        dirs.clear()
                        continue
                    rel = os.path.relpath(root, path)
                    prefix = "" if rel == "." else rel + "/"
                    for d in sorted(dirs):
                        if d.startswith("."):
                            continue
                        entries.append(f"{prefix}{d}/")
                    for f in sorted(files):
                        if f.startswith("."):
                            continue
                        entries.append(f"{prefix}{f}")
            else:
                for entry in sorted(os.listdir(path)):
                    if entry.startswith("."):
                        continue
                    full = os.path.join(path, entry)
                    suffix = "/" if os.path.isdir(full) else ""
                    entries.append(f"{entry}{suffix}")

            if not entries:
                return "(empty directory)"
            return "\n".join(entries)
        except Exception as e:
            return f"Error listing directory: {e}"

    async def _tool_edit_file(self, params: dict) -> str:
        """Perform an exact string replacement in a file."""
        path = self._resolve_path(params.get("path", ""))
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")
        replace_all = params.get("replace_all", False)

        if not old_string:
            return "Error: old_string cannot be empty"
        if old_string == new_string:
            return "Error: old_string and new_string are identical"
        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        if not os.path.isfile(path):
            return f"Error: Not a file: {path}"

        try:
            with open(path, "r", errors="replace") as f:
                content = f.read()

            count = content.count(old_string)
            if count == 0:
                return (
                    f"Error: old_string not found in {path}. "
                    "Check whitespace, indentation, and line endings."
                )
            if count > 1 and not replace_all:
                return (
                    f"Error: old_string appears {count} times in {path}. "
                    "Include more surrounding context to make it unique, "
                    "or set replace_all=true."
                )

            new_content = content.replace(old_string, new_string)
            with open(path, "w") as f:
                f.write(new_content)

            applied = count if replace_all else 1
            return f"Edited {path} ({applied} replacement{'s' if applied != 1 else ''})"
        except Exception as e:
            return f"Error editing file: {e}"

    async def _tool_multi_edit(self, params: dict) -> str:
        """Apply multiple edits to a file atomically."""
        path = self._resolve_path(params.get("path", ""))
        edits = params.get("edits", [])

        if not edits:
            return "Error: no edits provided"
        if not os.path.exists(path):
            return f"Error: File not found: {path}"
        if not os.path.isfile(path):
            return f"Error: Not a file: {path}"

        try:
            with open(path, "r", errors="replace") as f:
                content = f.read()
            original = content

            for i, edit in enumerate(edits, 1):
                old_s = edit.get("old_string", "")
                new_s = edit.get("new_string", "")
                replace_all = edit.get("replace_all", False)
                if not old_s:
                    return f"Error (edit #{i}): old_string cannot be empty"
                if old_s == new_s:
                    return f"Error (edit #{i}): old_string and new_string are identical"
                cnt = content.count(old_s)
                if cnt == 0:
                    return f"Error (edit #{i}): old_string not found. File unchanged."
                if cnt > 1 and not replace_all:
                    return (
                        f"Error (edit #{i}): old_string appears {cnt} times. "
                        "Add more context or set replace_all=true. File unchanged."
                    )
                content = content.replace(old_s, new_s)

            # All edits succeeded — write atomically
            with open(path, "w") as f:
                f.write(content)
            return f"Applied {len(edits)} edits to {path}"
        except Exception as e:
            return f"Error in multi_edit: {e}"

    async def _tool_grep(self, params: dict) -> str:
        """Search file contents with ripgrep (falls back to grep)."""
        pattern = params.get("pattern", "")
        if not pattern:
            return "Error: pattern cannot be empty"

        search_path = self._resolve_path(params.get("path") or self.workspace_dir)
        glob_filter = params.get("glob", "")
        case_insensitive = params.get("case_insensitive", False)
        max_results = min(params.get("max_results", 100), 1000)

        rg = shutil.which("rg")
        if rg:
            cmd = [rg, "--line-number", "--no-heading", "--color=never", "-H"]
            if case_insensitive:
                cmd.append("-i")
            if glob_filter:
                cmd.extend(["--glob", glob_filter])
            cmd.extend(["--max-count", str(max_results), pattern, search_path])
        else:
            # Fallback to grep -r
            cmd = ["grep", "-rn", "--color=never"]
            if case_insensitive:
                cmd.append("-i")
            if glob_filter:
                cmd.extend(["--include", glob_filter])
            cmd.extend(["-m", str(max_results), pattern, search_path])

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
            output = stdout.decode("utf-8", errors="replace")
            # Exit code 1 = no matches (both rg and grep)
            if process.returncode == 1:
                return "(no matches)"
            if process.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return f"Error (exit {process.returncode}): {err or 'search failed'}"

            lines = output.splitlines()
            if len(lines) > max_results:
                lines = lines[:max_results]
                lines.append(f"... (truncated at {max_results} matches)")
            result = "\n".join(lines)
            if len(result) > MAX_OUTPUT_CHARS:
                result = result[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
            return result or "(no matches)"
        except asyncio.TimeoutError:
            return "Error: grep timed out after 30s"
        except Exception as e:
            return f"Error running grep: {e}"

    async def _tool_glob(self, params: dict) -> str:
        """Find files by glob pattern, sorted by mtime (newest first)."""
        pattern = params.get("pattern", "")
        if not pattern:
            return "Error: pattern cannot be empty"
        root = self._resolve_path(params.get("path") or self.workspace_dir)
        try:
            # Absolute pattern: use as-is. Relative: join with root.
            if os.path.isabs(pattern):
                matches = _glob.glob(pattern, recursive=True)
            else:
                matches = _glob.glob(os.path.join(root, pattern), recursive=True)
            # Filter out directories, sort by mtime desc
            files = [m for m in matches if os.path.isfile(m)]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            if not files:
                return "(no matches)"
            if len(files) > 500:
                files = files[:500]
                files.append(f"... (truncated at 500 files)")
            return "\n".join(files)
        except Exception as e:
            return f"Error running glob: {e}"

    async def _tool_web_search(self, params: dict) -> str:
        """Search the web using DuckDuckGo (no API key needed)."""
        query = params.get("query", "").strip()
        max_results = min(params.get("max_results", 5), 10)
        if not query:
            return "Error: query cannot be empty"

        try:
            import httpx
            # DuckDuckGo HTML search
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                },
            ) as client:
                response = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                )
                response.raise_for_status()
                html = response.text

            # Parse results from DDG HTML
            import re
            results = []
            # Extract result blocks: <a class="result__a" href="...">title</a> + <a class="result__snippet">snippet</a>
            blocks = re.findall(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span)',
                html, re.DOTALL
            )
            for url, title, snippet in blocks[:max_results]:
                # Clean HTML from title/snippet
                title = re.sub(r"<[^>]+>", "", title).strip()
                snippet = re.sub(r"<[^>]+>", "", snippet).strip()
                # DDG wraps URLs in a redirect — extract the real URL
                real_url = url
                if "uddg=" in url:
                    match = re.search(r"uddg=([^&]+)", url)
                    if match:
                        from urllib.parse import unquote
                        real_url = unquote(match.group(1))
                results.append(f"**{title}**\n{real_url}\n{snippet}")

            if not results:
                return f"No results found for '{query}'. Try different search terms."

            return f"Search results for '{query}':\n\n" + "\n\n---\n\n".join(results)
        except Exception as e:
            return f"Error searching for '{query}': {e}"

    async def _tool_web_fetch(self, params: dict) -> str:
        """Fetch a URL and return its content as readable text."""
        url = params.get("url", "").strip()
        max_chars = min(params.get("max_chars", 20000), 100000)
        if not url:
            return "Error: url cannot be empty"
        if not url.startswith(("http://", "https://")):
            return "Error: url must start with http:// or https://"

        try:
            import httpx
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30,
                headers={"User-Agent": "AI-Employee-Agent/1.0"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                text = response.text

            # Strip HTML if needed
            if "html" in content_type or text.lstrip().startswith("<"):
                text = _html_to_text(text)

            if len(text) > max_chars:
                text = text[:max_chars] + f"\n... (truncated, {len(text)} total chars)"
            return text
        except Exception as e:
            return f"Error fetching {url}: {e}"

    async def _tool_git_status(self, params: dict) -> str:
        """Run git status in porcelain format."""
        repo = self._resolve_path(params.get("path") or self.workspace_dir)
        return await _run_git(["status", "--short", "--branch"], repo)

    async def _tool_git_diff(self, params: dict) -> str:
        """Run git diff with common options."""
        repo = self._resolve_path(params.get("path") or self.workspace_dir)
        args = ["diff", "--no-color"]
        if params.get("staged"):
            args.append("--staged")
        ref = params.get("ref")
        if ref:
            args.append(ref)
        file = params.get("file")
        if file:
            args.append("--")
            args.append(file)
        return await _run_git(args, repo)

    async def close(self) -> None:
        """Clean up resources."""
        if self._api_client:
            await self._api_client.close()
        if self._mcp_client:
            await self._mcp_client.close()


async def _run_git(args: list[str], cwd: str) -> str:
    """Helper to run a git command and return its output."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if process.returncode != 0:
            return f"git error (exit {process.returncode}): {err.strip() or output.strip()}"
        result = output if output else "(no output)"
        if len(result) > MAX_OUTPUT_CHARS:
            result = result[:MAX_OUTPUT_CHARS] + "\n... (truncated)"
        return result
    except asyncio.TimeoutError:
        return "Error: git command timed out"
    except FileNotFoundError:
        return "Error: git not installed"
    except Exception as e:
        return f"Error running git: {e}"


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n\s*\n\s*\n+")


def _html_to_text(html: str) -> str:
    """Minimal HTML → plain text without adding dependencies."""
    # Strip script/style blocks entirely
    text = _SCRIPT_STYLE_RE.sub("", html)
    # Convert common block tags to newlines BEFORE stripping tags
    text = re.sub(r"</(p|div|br|h[1-6]|li|tr|section|article)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = _TAG_RE.sub("", text)
    # Decode HTML entities
    import html as _html
    text = _html.unescape(text)
    # Collapse excessive blank lines
    text = _WS_RE.sub("\n\n", text)
    return text.strip()
