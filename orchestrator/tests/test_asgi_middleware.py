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

import app.core.auth as auth_module
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient

from app.main import APIRateLimitMiddleware, SecurityHeadersMiddleware


def _ok(request):
    return PlainTextResponse("ok")


async def _ws(websocket):
    await websocket.accept()
    await websocket.send_text("ok")
    await websocket.close()


def _build(middleware):
    routes = [
        Route("/x", _ok),
        Route("/health", _ok),
        WebSocketRoute("/ws", _ws),
    ]
    return TestClient(Starlette(routes=routes, middleware=middleware))


class _FakeRedisClient:
    """Minimal async Redis stub for the distributed rate-limit branch."""

    def __init__(self, ttl: int = 42):
        self._counts: dict[str, int] = {}
        self._ttl = ttl

    async def incr(self, key):
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key, seconds):
        return True

    async def ttl(self, key):
        return self._ttl


class _FakeRedisSvc:
    def __init__(self, client):
        self.client = client


def _build_with_redis(middleware, redis_client, routes=None):
    routes = routes or [Route("/x", _ok)]
    app = Starlette(routes=routes, middleware=middleware)
    app.state.redis = _FakeRedisSvc(redis_client)
    return TestClient(app, raise_server_exceptions=True)


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

    def test_websocket_upgrade_skips_rate_limit(self):
        # An Upgrade: websocket handshake must never be counted or blocked, even
        # when the per-key budget is already exhausted by prior HTTP traffic.
        client = _build(
            [Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60)]
        )
        assert client.get("/x").status_code == 200
        assert client.get("/x").status_code == 429  # budget now exhausted
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_text() == "ok"

    def test_jwt_cookie_keys_per_user_with_ip_fallback(self, monkeypatch):
        # Callers are bucketed by user:<sub> from the JWT cookie; a valid token
        # for a *different* user gets its own budget, and an undecodable token
        # falls back to the shared IP bucket.
        def fake_decode(token):
            if token == "bad":
                raise ValueError("invalid token")
            return {"sub": token}  # token string doubles as the subject

        monkeypatch.setattr(auth_module, "decode_token", fake_decode)

        client = _build(
            [Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60)]
        )

        def _get(token=None):
            client.cookies.clear()
            if token is not None:
                client.cookies.set("access_token", token)
            return client.get("/x")

        # alice: first ok, second blocked (own bucket).
        assert _get("alice").status_code == 200
        assert _get("alice").status_code == 429
        # bob: independent bucket, still allowed.
        assert _get("bob").status_code == 200
        # invalid token -> IP bucket (untouched so far) -> allowed, then blocked.
        assert _get("bad").status_code == 200
        assert _get("bad").status_code == 429

    def test_redis_path_retry_after_from_ttl(self):
        # When Redis backs the limiter, Retry-After echoes the key's remaining TTL.
        redis = _FakeRedisClient(ttl=42)
        client = _build_with_redis(
            [Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60)],
            redis,
        )
        assert client.get("/x").status_code == 200
        blocked = client.get("/x")
        assert blocked.status_code == 429
        assert blocked.headers.get("Retry-After") == "42"

    def test_redis_branch_does_not_double_call_app_on_downstream_error(self):
        # Regression for #346: a downstream 500 in the Redis branch must propagate,
        # not be swallowed and re-dispatched (which double-checks-out a DB conn).
        calls = {"n": 0}

        def _boom(request):
            calls["n"] += 1
            raise RuntimeError("downstream failure")

        redis = _FakeRedisClient()
        client = _build_with_redis(
            [Middleware(APIRateLimitMiddleware, max_requests=10, window_seconds=60)],
            redis,
            routes=[Route("/x", _boom)],
        )
        try:
            client.get("/x")
        except RuntimeError:
            pass  # propagation is the expected behaviour
        assert calls["n"] == 1  # app invoked exactly once, no re-dispatch


class TestCombinedStack:
    def test_429_carries_no_security_headers(self):
        # Production order: RateLimit wraps SecurityHeaders (which wraps the app).
        # A 429 returned by RateLimit short-circuits before SecurityHeaders runs,
        # so the rejection response must not carry any security headers.
        client = _build(
            [
                Middleware(APIRateLimitMiddleware, max_requests=1, window_seconds=60),
                Middleware(SecurityHeadersMiddleware),
            ]
        )
        ok = client.get("/x")
        assert ok.status_code == 200
        assert ok.headers["X-Frame-Options"] == "DENY"  # inner mw ran on success

        blocked = client.get("/x")
        assert blocked.status_code == 429
        assert "X-Frame-Options" not in blocked.headers
        assert "Content-Security-Policy" not in blocked.headers
