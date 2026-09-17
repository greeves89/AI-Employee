from aiohttp import web


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "healthy",
            "agent_id": request.app["agent_id"],
        }
    )


def _arg_parse_selftest() -> bool:
    """Return True if the RUNNING image parses Responses-API tool arguments that
    arrive only on the final ``.done`` event (the #342/#285 regression case).

    A stale/rolled-back image that predates the fix returns False here even while
    the source on ``main`` is correct — this is the signal that distinguishes a
    deploy regression from a code regression.
    """
    from app.providers.openai_provider import OpenAIProvider

    parsed = OpenAIProvider._parse_function_arguments("", '{"content":"kept"}')
    return parsed == {"content": "kept"}


async def diag_handler(request: web.Request) -> web.Response:
    """Runtime self-diagnostic — surfaces whether the deployed image is affected
    by the MCP arg-stripping regression (#342). Returns HTTP 500 when degraded so
    a health probe / monitor can page on a stale deploy."""
    arg_parse_ok = _arg_parse_selftest()
    body = {
        "agent_id": request.app["agent_id"],
        "checks": {"mcp_arg_parse": "ok" if arg_parse_ok else "FAILED"},
    }
    return web.json_response(body, status=200 if arg_parse_ok else 500)


async def pretooluse_hook_handler(request: web.Request) -> web.Response:
    """Claude Code PreToolUse HTTP hook target (issue #197, Teil 2).

    Registered in the agent's own ``.claude/settings.json`` (written by
    ``agent_manager.py`` — only for ``mode=claude_code`` agents), fired by
    the ``claude`` CLI itself for every tool call, native and MCP alike.
    Local-only: the hook runs inside the SAME container and calls back into
    this same process's own health server, so no network hop leaves the box.
    """
    try:
        body = await request.json()
    except Exception:
        # Malformed input from the hook caller is not this agent's fault to
        # diagnose — fail closed rather than guess.
        return web.json_response(_deny_malformed())
    tool_name = body.get("tool_name") or ""
    tool_input = body.get("tool_input") or {}
    from app.tools.pretooluse_hook import decide_async
    return web.json_response(await decide_async(tool_name, tool_input))


def _deny_malformed() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "PreToolUse-Hook erhielt kein lesbares JSON.",
        }
    }


async def start_health_server(agent_id: str, port: int = 8080) -> web.AppRunner:
    app = web.Application()
    app["agent_id"] = agent_id
    app.router.add_get("/health", health_handler)
    app.router.add_get("/diag", diag_handler)
    app.router.add_post("/hooks/pretooluse", pretooluse_hook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
