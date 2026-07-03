"""Security-hardening regression tests (Security-Audit 2026-07-01).

Cover the four fixes on branch ``harden/kiosk-memory-auth``:
  1. ``POST /agents/{id}/telegram/send`` requires an agent token
  2. ``GET /memory/preload/{id}`` requires user/agent auth
  3. the kiosk router is only mounted when ``settings.kiosk_enabled``
  4. setup mode is fail-closed on real DB errors (no anonymous ADMIN)

These import the app package, so they run in the orchestrator container / CI
(where fastapi + sqlalchemy are installed), not on a bare host. They are
DB-free by design: they assert the wiring (route dependencies, settings,
error handling), not full HTTP round-trips.
"""
import asyncio


def _route_dependency_names(router, path_suffix: str, method: str) -> list[str]:
    """Names of the callables the matching route depends on (Depends(...))."""
    names: list[str] = []
    for route in router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path.endswith(path_suffix) and method in methods:
            for dep in route.dependant.dependencies:
                call = getattr(dep, "call", None)
                if call is not None:
                    names.append(call.__name__)
    return names


def test_kiosk_enabled_defaults_false():
    from app.config import settings
    assert settings.kiosk_enabled is False


def test_kiosk_router_not_mounted_when_disabled():
    import importlib
    from app.config import settings
    settings.kiosk_enabled = False
    from app.api import router as router_mod
    importlib.reload(router_mod)
    paths = [getattr(r, "path", "") for r in router_mod.api_router.routes]
    assert not any("/kiosk" in p for p in paths)


def test_telegram_send_requires_agent_token():
    from app.api import agents
    names = _route_dependency_names(agents.router, "/telegram/send", "POST")
    assert "verify_agent_token" in names


def test_memory_preload_requires_auth():
    from app.api import memory
    names = _route_dependency_names(memory.router, "/preload/{agent_id}", "GET")
    assert "require_auth_or_agent" in names


def test_check_users_exist_is_fail_closed_on_db_error():
    from sqlalchemy.exc import OperationalError, ProgrammingError
    from app import dependencies

    class _FakeSession:
        def __init__(self, exc):
            self._exc = exc

        async def scalar(self, *args, **kwargs):
            raise self._exc

    # A genuinely missing users table -> real setup mode -> False.
    prog = ProgrammingError("SELECT", {}, Exception("relation \"users\" does not exist"))
    assert asyncio.run(dependencies._check_users_exist(_FakeSession(prog))) is False

    # Any other DB error (connection down/timeout) must PROPAGATE, so the caller
    # denies the request instead of granting an anonymous ADMIN with RLS bypass.
    op = OperationalError("SELECT", {}, Exception("connection refused"))
    raised = False
    try:
        asyncio.run(dependencies._check_users_exist(_FakeSession(op)))
    except OperationalError:
        raised = True
    assert raised
