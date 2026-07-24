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

from app.core.auth import create_access_token
from app.main import APIRateLimitMiddleware, SecurityHeadersMiddleware


def _ok(request):
    return PlainTextResponse("ok")


def _build(middleware):
    routes = [Route("/x", _ok), Route("/health", _ok)]
    return TestClient(Starlette(routes=routes, middleware=middleware))


class _FakeRedisClient:
    """Minimal async stand-in for the redis client used by the middleware."""

    def __init__(self, count: int, ttl: int):
        self._count = count
        self._ttl = ttl

    async def incr(self, key):
        return self._count

    async def expire(self, key, window):
        return True

    async def ttl(self, key):
        return self._ttl


class _FakeRedisSvc:
    def __init__(self, client):
        self.client = client


def _build_with_redis(count, ttl, max_requests=2):
    routes = [Route("/x", _ok), Route("/health", _ok)]
    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(
                APIRateLimitMiddleware, max_requests=max_requests, window_seconds=60
            )
        ],
    )
    app.state.redis = _FakeRedisSvc(_FakeRedisClient(count, ttl))
    return TestClient(app)


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

    def test_websocket_upgrade_is_skipped(self):
        client = _build(
            [Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60)]
        )
        # An Upgrade: websocket handshake must bypass rate limiting entirely.
        for _ in range(5):
            r = client.get("/x", headers={"Upgrade": "websocket"})
            assert r.status_code == 200

    def test_jwt_cookie_keys_per_user_with_ip_fallback(self):
        client = _build(
            [Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60)]
        )
        alice = create_access_token("alice", "user")
        bob = create_access_token("bob", "user")
        # Alice: first request ok, second over her per-user bucket -> 429.
        assert (
            client.get("/x", headers={"Cookie": f"access_token={alice}"}).status_code
            == 200
        )
        assert (
            client.get("/x", headers={"Cookie": f"access_token={alice}"}).status_code
            == 429
        )
        # Bob is keyed as user:bob — a separate bucket, still allowed.
        assert (
            client.get("/x", headers={"Cookie": f"access_token={bob}"}).status_code
            == 200
        )
        # An invalid token falls back to IP keying (its own bucket).
        assert (
            client.get("/x", headers={"Cookie": "access_token=garbage"}).status_code
            == 200
        )

    def test_redis_path_retry_after_from_ttl(self):
        # Redis reports the caller is over the limit; Retry-After must reflect ttl.
        client = _build_with_redis(count=99, ttl=42, max_requests=2)
        r = client.get("/x")
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "42"
        assert "Rate limit exceeded" in r.text

    def test_redis_path_under_limit_passes_through(self):
        client = _build_with_redis(count=1, ttl=60, max_requests=2)
        assert client.get("/x").status_code == 200

    def test_redis_path_retry_after_is_floored_to_one(self):
        # ttl == -1 (no expiry set) must not produce a non-positive Retry-After.
        client = _build_with_redis(count=99, ttl=-1, max_requests=2)
        r = client.get("/x")
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "1"


class TestCombinedStack:
    def test_429_carries_no_security_headers(self):
        # RateLimit outermost, SecurityHeaders inner — mirrors main.py ordering.
        routes = [Route("/x", _ok)]
        app = Starlette(
            routes=routes,
            middleware=[
                Middleware(
                    APIRateLimitMiddleware, max_requests=1, window_seconds=60
                ),
                Middleware(SecurityHeadersMiddleware),
            ],
        )
        client = TestClient(app)
        # First request reaches the app through both layers -> headers stamped.
        first = client.get("/x")
        assert first.status_code == 200
        assert first.headers["X-Frame-Options"] == "DENY"
        # Second is short-circuited by RateLimit before SecurityHeaders runs.
        blocked = client.get("/x")
        assert blocked.status_code == 429
        assert "X-Frame-Options" not in blocked.headers
        assert "Content-Security-Policy" not in blocked.headers
