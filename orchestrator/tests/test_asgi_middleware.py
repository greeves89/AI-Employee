"""Regression tests for the pure-ASGI security & rate-limit middleware.

Both middlewares used to subclass Starlette's ``BaseHTTPMiddleware``, which runs
the downstream app inside its own anyio task/cancel-scope. When a client
disconnected mid-request, that inner task was cancelled while an endpoint still
held a checked-out SQLAlchemy connection, orphaning it — the pool then logged
``non-checked-in connection ... will be terminated`` (seen 2026-07-23). Rewriting
them as pure ASGI keeps the request on the original task so cancellation unwinds
the DB session cleanly. These tests pin the observable behaviour (headers +
rate-limit responses) so the pure-ASGI implementation stays correct.
"""

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.main import APIRateLimitMiddleware, SecurityHeadersMiddleware


def _ok(request):
    return PlainTextResponse("ok")


def _build(middleware):
    routes = [Route("/x", _ok), Route("/health", _ok)]
    return TestClient(Starlette(routes=routes, middleware=middleware))


class TestSecurityHeaders:
    def test_headers_are_stamped(self):
        client = _build([Middleware(SecurityHeadersMiddleware)])
        r = client.get("/x")
        assert r.status_code == 200
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert r.headers["X-XSS-Protection"] == "1; mode=block"
        assert "camera=()" in r.headers["Permissions-Policy"]
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


class TestRateLimit:
    def test_allows_up_to_limit_then_429(self):
        client = _build(
            [Middleware(APIRateLimitMiddleware, max_requests=2, window_seconds=60)]
        )
        assert client.get("/x").status_code == 200
        assert client.get("/x").status_code == 200
        blocked = client.get("/x")
        assert blocked.status_code == 429
        assert blocked.headers.get("Retry-After") == "60"
        assert "Rate limit exceeded" in blocked.text

    def test_health_is_never_rate_limited(self):
        client = _build(
            [Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60)]
        )
        # Far more than the limit — health must always pass through.
        for _ in range(5):
            assert client.get("/health").status_code == 200
