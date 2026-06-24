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
    # Second Brain mutations (write category covers contribute/update/delete)
    "brain_contribute": "knowledge_write",
    "brain_update": "knowledge_write",
    "brain_delete": "knowledge_write",
    # Package installation
    "install_package": "system_config",
}

# These are ALWAYS allowed regardless of whitelist
ALWAYS_ALLOWED_TOOLS = frozenset({
    "request_approval",
    "rate_task",
    "memory_save", "memory_search", "memory_list", "memory_delete",
    "read_file", "list_files", "glob", "grep", "view_image",
    "git_status", "git_diff",
    "web_search", "web_fetch",
    "brain_search", "brain_get", "brain_list", "brain_related",
    "list_team", "list_tasks", "list_todos", "list_schedules",
    "skill_search", "skill_get_my_skills", "skill_install", "skill_rate",
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
    "read_file", "list_files", "glob", "grep", "view_image",
    "git_status", "git_diff",
    "web_search", "web_fetch",
    "memory_search", "brain_search", "brain_get", "brain_list", "brain_related", "list_team",
    "list_tasks", "list_todos", "list_schedules",
})

# Subset of concurrent-safe tools whose results are worth caching
_CACHEABLE_TOOLS = frozenset({
    "read_file", "list_files", "glob", "grep",
    "memory_search", "brain_search", "brain_list", "list_team",
    "list_tasks", "list_todos", "list_schedules",
})
_CACHE_TTL_SECONDS = 120  # 2 minutes (tool result cache)
_AUTONOMY_CACHE_TTL = 10  # 10s — fast enough for level changes to propagate
_COMMAND_POLICY_CACHE_TTL = 10


async def _get_command_policies() -> list[dict]:
    """Fetch active command policies for this agent.

    Fail-open by design: transient orchestrator outages should not brick agents.
    The command policy API is cached briefly, so UI changes propagate quickly
    without adding network latency to every bash call.
    """
    now = time.time()
    cached = _get_command_policies._cache
    if cached and now < cached[1]:
        return cached[0]

    try:
        import httpx

        url = f"{settings.orchestrator_url}/api/v1/command-policies/for-agent/{settings.agent_id}"
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(
                url,
                headers={
                    "X-Agent-ID": settings.agent_id,
                    "Authorization": f"Bearer {settings.agent_token}",
                },
            )
        response.raise_for_status()
        policies = response.json().get("policies", [])
        if not isinstance(policies, list):
            policies = []
        _get_command_policies._cache = (policies, now + _COMMAND_POLICY_CACHE_TTL)
        return policies
    except Exception as exc:  # noqa: BLE001
        logger.warning("Command policy fetch failed; allowing bash command: %s", exc)
        _get_command_policies._cache = ([], now + _COMMAND_POLICY_CACHE_TTL)
        return []


_get_command_policies._cache = ([], 0.0)


async def _evaluate_command_policy(command: str) -> tuple[str | None, str | None]:
    """Return (effect, reason) for the first matching policy."""
    for policy in await _get_command_policies():
        pattern = str(policy.get("pattern") or "")
        if not pattern:
            continue
        try:
            if re.search(pattern, command, re.IGNORECASE | re.MULTILINE):
                effect = str(policy.get("effect") or "blocked").lower()
                if effect not in {"blocked", "high", "medium", "allow"}:
                    effect = "blocked"
                reason = policy.get("description") or policy.get("name") or pattern
                return effect, str(reason)
        except re.error as exc:
            logger.warning("Invalid command policy regex %r: %s", pattern, exc)
    return None, None


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
        # File-state tracking: realpath → mtime recorded when the agent reads
        # (or writes) a file. Edits are gated on having an up-to-date read,
        # so the model can't blindly overwrite a file it never looked at.
        self._read_files: dict[str, float] = {}

    def _record_file_state(self, resolved_path: str) -> None:
        """Mark a file as freshly seen by the agent (after a read or a write)."""
        try:
            self._read_files[os.path.realpath(resolved_path)] = os.path.getmtime(resolved_path)
        except OSError:
            pass

    def _check_editable(self, resolved_path: str) -> str | None:
        """Return an error string if the agent may not edit this file yet.

        A non-existent file is fine (it's a create). An existing file must
        have been read first, and must not have changed since that read.
        """
        rp = os.path.realpath(resolved_path)
        if not os.path.exists(rp):
            return None  # creating a new file — no prior read required
        recorded = self._read_files.get(rp)
        if recorded is None:
            return (
                f"Error: you must read this file with read_file before "
                f"modifying it: {resolved_path}"
            )
        try:
            if abs(os.path.getmtime(rp) - recorded) > 1e-6:
                return (
                    f"Error: {resolved_path} changed since you last read it. "
                    f"Call read_file on it again before modifying it."
                )
        except OSError:
            pass
        return None

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

        # Default 120s (was 30s — too short for video renders, builds, installs),
        # hard cap 600s. The model can request a longer timeout up to the cap.
        timeout = min(params.get("timeout", 120), 600)
        policy_effect, policy_reason = await _evaluate_command_policy(command)
        if policy_effect == "blocked":
            return (
                "[COMMAND BLOCKED]\n"
                f"Command: {command}\n"
                f"Policy: {policy_reason or 'Blocked by command policy'}\n"
                "This command cannot be approved or executed."
            )
        if policy_effect in {"high", "medium"}:
            approval_error = await self._request_command_policy_approval(
                command=command,
                effect=policy_effect,
                reason=policy_reason or "Matched command policy",
                timeout=timeout,
            )
            if approval_error:
                return approval_error

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
            hint = (
                f" If this was a long-running build/render that just needs more time, "
                f"re-run it with a higher timeout, e.g. timeout={min(timeout * 2, 600)} (max 600s)."
                if timeout < 600 else ""
            )
            return f"Error: Command timed out after {timeout}s.{hint}"

    async def _request_command_policy_approval(
        self,
        command: str,
        effect: str,
        reason: str,
        timeout: int,
    ) -> str | None:
        """Create and wait for an approval generated by a command policy.

        Returning None means approved; returning a string means the bash tool
        should stop and surface that message to the model/user.
        """
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                create_resp = await client.post(
                    f"{settings.orchestrator_url}/api/v1/approvals/request",
                    headers={
                        "X-Agent-ID": settings.agent_id,
                        "Authorization": f"Bearer {settings.agent_token}",
                    },
                    json={
                        "tool": "bash",
                        "input": {"command": command},
                        "reasoning": f"Command policy matched: {reason}",
                        "risk_level": effect,
                        "target_channel": "all",
                    },
                )
                create_resp.raise_for_status()
                approval_id = str(create_resp.json().get("approval_id") or "")
                if not approval_id:
                    return "Error: Command policy approval request did not return an approval id."

                wait_seconds = min(max(timeout, 60), 300)
                deadline = time.time() + wait_seconds
                while time.time() < deadline:
                    await asyncio.sleep(2)
                    check_resp = await client.get(
                        f"{settings.orchestrator_url}/api/v1/approvals/check/{approval_id}",
                        headers={
                            "X-Agent-ID": settings.agent_id,
                            "Authorization": f"Bearer {settings.agent_token}",
                        },
                    )
                    check_resp.raise_for_status()
                    status = str(check_resp.json().get("status") or "").lower()
                    if status == "approved":
                        return None
                    if status == "denied":
                        return f"[COMMAND DENIED]\nCommand: {command}\nPolicy: {reason}"
                return (
                    "[COMMAND APPROVAL PENDING]\n"
                    f"Command: {command}\n"
                    f"Policy: {reason}\n"
                    f"Approval {approval_id} is still pending. Wait for the user decision before retrying."
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Command policy approval failed for %r: %s", command, exc)
            return f"Error: command requires approval, but the approval request failed: {exc}"

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

            # Record that the agent has now seen this file — unlocks editing.
            self._record_file_state(path)

            if len(content) > MAX_OUTPUT_CHARS:
                content = content[:MAX_OUTPUT_CHARS] + f"\n... (truncated, {len(content)} total chars)"
            return content
        except Exception as e:
            return f"Error reading file: {e}"

    async def _tool_view_image(self, params: dict) -> str:
        """Load an image so the model can see it (vision).

        Returns a sentinel-prefixed result the providers render as a real
        image content block. Accepts a workspace path, a Telegram file_id,
        or an http(s) URL.
        """
        from app import multimodal

        path = (params.get("path") or "").strip()
        file_id = (params.get("file_id") or "").strip()
        url = (params.get("url") or "").strip()

        data: bytes
        media_type: str
        source_label: str

        try:
            if path:
                resolved = self._resolve_path(path)
                if not os.path.isfile(resolved):
                    return f"Error: image file not found: {resolved}"
                with open(resolved, "rb") as f:
                    data = f.read()
                media_type = multimodal.guess_media_type(resolved)
                source_label = os.path.basename(resolved)
            elif file_id:
                import httpx
                api_url = f"{settings.orchestrator_url}/api/v1/telegram/get-file"
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        api_url,
                        json={"file_id": file_id},
                        headers={
                            "X-Agent-ID": settings.agent_id,
                            "Authorization": f"Bearer {settings.agent_token}",
                        },
                    )
                if resp.status_code != 200:
                    return f"Error: could not fetch Telegram file (HTTP {resp.status_code}): {resp.text[:300]}"
                meta = resp.json()
                import base64 as _b64
                data = _b64.b64decode(meta.get("file_base64", ""))
                media_type = multimodal.guess_media_type(meta.get("filename", ""))
                source_label = meta.get("filename", "telegram image")
            elif url:
                if not url.startswith(("http://", "https://")):
                    return "Error: url must start with http:// or https://"
                blocked = await self._check_url_allowlist(url)
                if blocked:
                    return blocked
                import httpx
                async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                    resp = await client.get(url, headers={"User-Agent": "AI-Employee-Agent/1.0"})
                if resp.status_code != 200:
                    return f"Error: could not fetch image URL (HTTP {resp.status_code})"
                data = resp.content
                ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                media_type = ct if ct in multimodal.SUPPORTED_MEDIA_TYPES else multimodal.guess_media_type(url)
                source_label = url
            else:
                return "Error: provide one of path, file_id, or url"
        except Exception as e:
            return f"Error loading image: {e}"

        if not data:
            return "Error: image is empty"
        if media_type not in multimodal.SUPPORTED_MEDIA_TYPES:
            return (
                f"Error: unsupported image type '{media_type}'. "
                f"Supported: {', '.join(sorted(multimodal.SUPPORTED_MEDIA_TYPES))}"
            )
        if len(data) > multimodal.MAX_IMAGE_BYTES:
            return (
                f"Error: image is {len(data) // 1024} KB, exceeds the "
                f"{multimodal.MAX_IMAGE_BYTES // 1024 // 1024} MB limit. "
                "Resize it first (e.g. with the bash tool + ImageMagick)."
            )

        note = f"Image loaded ({source_label}, {len(data) // 1024} KB). Analyze its visual content above."
        return multimodal.encode_image_result(data, media_type, note)

    async def _tool_present_image(self, params: dict) -> str:
        """Show an image file to the user (chat UI + optionally Telegram).

        Returns a sentinel-prefixed result; the chat handler detects it and
        emits an ``image`` event so the UI renders it inline. The model also
        sees the image (confirmation of what it presented).
        """
        from app import multimodal

        path = (params.get("path") or "").strip()
        caption = (params.get("caption") or "").strip()
        send_telegram = bool(params.get("send_telegram", False))

        if not path:
            return "Error: path is required"
        resolved = self._resolve_path(path)
        if not os.path.isfile(resolved):
            return f"Error: image file not found: {resolved}"
        try:
            with open(resolved, "rb") as f:
                data = f.read()
        except Exception as e:
            return f"Error reading image: {e}"

        media_type = multimodal.guess_media_type(resolved)
        if not data:
            return "Error: image is empty"
        if media_type not in multimodal.SUPPORTED_MEDIA_TYPES:
            return (
                f"Error: unsupported image type '{media_type}'. "
                f"Supported: {', '.join(sorted(multimodal.SUPPORTED_MEDIA_TYPES))}"
            )
        if len(data) > multimodal.MAX_IMAGE_BYTES:
            return (
                f"Error: image is {len(data) // 1024} KB, exceeds the "
                f"{multimodal.MAX_IMAGE_BYTES // 1024 // 1024} MB limit. Resize it first."
            )

        telegram_note = ""
        if send_telegram:
            client = self._get_api_client()
            tg_result = await client.send_telegram(
                {"message": caption or "📊", "file_path": resolved}
            )
            telegram_note = f" Telegram: {tg_result}"

        note = (caption or f"Presented {os.path.basename(resolved)}") + (
            f" ({len(data) // 1024} KB)"
        ) + telegram_note
        return multimodal.encode_image_result(data, media_type, note)

    async def _tool_present_file(self, params: dict) -> str:
        """Show a workspace file to the user as a downloadable chat attachment."""
        import mimetypes

        path = (params.get("path") or "").strip()
        caption = (params.get("caption") or "").strip()
        if not path:
            return "Error: path is required"
        resolved = self._resolve_path(path)
        if not os.path.isfile(resolved):
            return f"Error: file not found: {resolved}"
        try:
            size = os.path.getsize(resolved)
        except Exception as e:
            return f"Error reading file: {e}"
        if size <= 0:
            return "Error: file is empty"
        if size > 50 * 1024 * 1024:
            return f"Error: file is {size // 1024 // 1024} MB, exceeds the 50 MB chat attachment limit"

        payload = {
            "path": resolved,
            "filename": os.path.basename(resolved),
            "media_type": mimetypes.guess_type(resolved)[0] or "application/octet-stream",
            "size": size,
            "caption": caption,
        }
        return "__AI_EMPLOYEE_PRESENT_FILE__" + json.dumps(payload)

    async def _tool_write_file(self, params: dict) -> str:
        """Write content to a file."""
        path = self._resolve_path(params.get("path", ""))
        content = params.get("content", "")

        # Overwriting an existing file requires having read it first.
        gate = self._check_editable(path)
        if gate:
            return gate

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            self._record_file_state(path)
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
        gate = self._check_editable(path)
        if gate:
            return gate

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
            self._record_file_state(path)

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
        gate = self._check_editable(path)
        if gate:
            return gate

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
            self._record_file_state(path)
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

        blocked = await self._check_url_allowlist(url)
        if blocked:
            return blocked

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

    async def _check_url_allowlist(self, url: str) -> str | None:
        """Check if URL is allowed by the agent's URL allowlist.

        Returns None if allowed, or an error string if blocked.
        Uses a short cache to avoid hitting the orchestrator on every fetch.
        """
        now = time.time()
        cached = getattr(self, "_url_allowlist_cache", (None, 0.0))
        if now < cached[1]:
            allowlist = cached[0]
        else:
            allowlist = await asyncio.to_thread(self._fetch_url_allowlist)
            self._url_allowlist_cache = (allowlist, now + _AUTONOMY_CACHE_TTL)

        if allowlist is None:
            return None

        from urllib.parse import urlparse
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            host = (parsed.hostname or "").lower().strip(".")
        except Exception:
            host = ""

        for pattern in allowlist:
            pattern = pattern.lower().strip(".")
            if pattern == "*":
                return None
            if pattern.startswith("*."):
                suffix = pattern[2:]
                if host == suffix or host.endswith("." + suffix):
                    return None
            elif host == pattern:
                return None

        return (
            f"[URL BLOCKED] '{url}' is not in your URL allowlist. "
            f"You MUST call `request_approval` with the URL and reason "
            f"before accessing non-whitelisted URLs."
        )

    def _fetch_url_allowlist(self) -> list[str] | None:
        """Fetch agent's URL allowlist from the orchestrator. Returns None if no restrictions."""
        try:
            import urllib.request as _req
            import json as _json
            api_url = f"{settings.orchestrator_url}/api/v1/url-allowlist/agent/{settings.agent_id}"
            req = _req.Request(api_url, headers={"X-Agent-Token": settings.agent_token})
            with _req.urlopen(req, timeout=3) as resp:
                data = _json.loads(resp.read())
            if not data.get("is_restricted"):
                return None
            return [e["url_pattern"] for e in data.get("entries", []) if e.get("is_active", True)]
        except Exception:
            return None

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
