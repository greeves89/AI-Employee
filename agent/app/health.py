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


async def start_health_server(agent_id: str, port: int = 8080) -> web.AppRunner:
    app = web.Application()
    app["agent_id"] = agent_id
    app.router.add_get("/health", health_handler)
    app.router.add_get("/diag", diag_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
