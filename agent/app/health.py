import os

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


async def _mcp_zustand() -> dict:
    """Antworten die eingebauten MCP-Server wirklich?

    Warum das hier steht: Am 22.09.2026 meldete ein Agent dem Nutzer, dass
    Orchestrator, Skills und Memory "nicht verbunden" seien. Sie waren es --
    alle zehn antworteten. Der Agent hatte die Diagnosewarnungen von
    ``claude mcp list`` gelesen und als Ausfall gedeutet.

    Ein Agent kann seine eigene Verdrahtung bis dahin nirgends NACHSEHEN; er
    kann sie nur aus Werkzeugfehlern und Warntexten erraten. Genau das ist hier
    schiefgegangen. Also gibt es jetzt eine Stelle mit einer klaren Antwort --
    fuer den Betreiber wie fuer den Agenten selbst.

    Gemessen wird der gemeinsame Prozess, nicht die CLI-Konfiguration: Ein
    Server, der antwortet, ist verbunden; ein Warntext ueber doppelte
    Eintraege sagt darueber nichts aus.
    """
    port = int(os.environ.get("MCP_HTTP_PORT") or 0)
    if not port:
        return {"modus": "einzelprozesse", "hinweis":
                "Kein gemeinsamer MCP-Prozess eingerichtet (MCP_HTTP_PORT nicht gesetzt)."}

    from app.main import _COMBINED_MCP_NAMES

    namen = list(_COMBINED_MCP_NAMES)
    erreichbar, tot = [], []

    import asyncio

    async def _pruefe(name: str) -> None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=2)
            writer.close()
            await writer.wait_closed()
            erreichbar.append(name)
        except Exception:  # noqa: BLE001 - genau das ist das Ergebnis
            tot.append(name)

    # Der gemeinsame Prozess bedient alle Namen; ein Verbindungsaufbau genuegt.
    await _pruefe(namen[0] if namen else "-")
    if erreichbar:
        erreichbar, tot = namen, []
    else:
        erreichbar, tot = [], namen

    return {
        "modus": "gemeinsamer prozess",
        "port": port,
        "erwartet": len(namen),
        "erreichbar": len(erreichbar),
        "fehlend": sorted(tot),
    }


async def diag_handler(request: web.Request) -> web.Response:
    """Runtime self-diagnostic — surfaces whether the deployed image is affected
    by the MCP arg-stripping regression (#342). Returns HTTP 500 when degraded so
    a health probe / monitor can page on a stale deploy."""
    arg_parse_ok = _arg_parse_selftest()
    mcp = await _mcp_zustand()
    mcp_ok = not mcp.get("fehlend")
    body = {
        "agent_id": request.app["agent_id"],
        "checks": {
            "mcp_arg_parse": "ok" if arg_parse_ok else "FAILED",
            "mcp_server": "ok" if mcp_ok else "FAILED",
        },
        "mcp": mcp,
    }
    return web.json_response(body, status=200 if (arg_parse_ok and mcp_ok) else 500)


async def pretooluse_hook_handler(request: web.Request) -> web.Response:
    """PreToolUse-Entscheidungspunkt fuer Claude Code und Codex (#197, #781).

    Beide CLIs rufen vor JEDEM Werkzeugaufruf den verwalteten Hook
    ``/opt/mcp/pretooluse-hook.mjs`` auf (eingetragen in
    ``/etc/claude-code/managed-settings.json`` und ``/etc/codex/requirements.toml``,
    root-eigen im Abbild, Quelle ``agent/richtlinie/``); das Skript fragt hier.
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
    from app.tools.push_sperre import LAUF_HEADER

    # Arbeitsverzeichnis und Lauf (Task) fuer die Push-Sperre: Claude Code schickt
    # ``cwd`` im Hook-Aufruf und den Lauf als Kopfzeile (aus AI_EMPLOYEE_LAUF,
    # ``allowedEnvVars``), der Codex-Hook beides im Aufruf.
    cwd = body.get("cwd") if isinstance(body.get("cwd"), str) else None
    lauf = request.headers.get(LAUF_HEADER) or body.get("lauf")
    lauf = lauf.strip() if isinstance(lauf, str) and lauf.strip() else None
    return web.json_response(await decide_async(tool_name, tool_input, cwd=cwd, lauf=lauf))


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
    # Bildstrom des Agenten-Browsers (#828). Nur erreichbar, wenn der Browser
    # ueberhaupt eingeschaltet ist -- sonst gibt es nichts zu zeigen, und eine
    # Route, die immer scheitert, verwirrt nur.
    if os.environ.get("COMPUTER_USE_BROWSER", "").lower() == "true":
        from app.browser_stream import audit_handler, stream_handler
        app.router.add_get("/browser/stream", stream_handler)
        # Pruefprotokoll (#828, Schritt 5) -- der Orchestrator reicht das
        # owner-geprueft durch (``GET /agents/{id}/browser/audit``), genauso
        # wie er den Bildstrom selbst durchreicht statt ihn nach aussen zu
        # oeffnen.
        app.router.add_get("/browser/audit", audit_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
