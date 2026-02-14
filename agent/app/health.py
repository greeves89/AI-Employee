from aiohttp import web


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "healthy",
            "agent_id": request.app["agent_id"],
        }
    )


async def start_health_server(agent_id: str, port: int = 8080) -> web.AppRunner:
    app = web.Application()
    app["agent_id"] = agent_id
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
