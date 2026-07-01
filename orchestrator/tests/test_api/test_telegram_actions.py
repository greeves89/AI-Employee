"""Unit tests for _tg_request in telegram_actions — error handling."""
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Stub heavy dependencies before importing telegram_actions ---

def _make_stub(name: str, **attrs):
    """Create and register a module stub, overwriting any existing entry."""
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

_sqlalchemy = _make_stub("sqlalchemy", select=MagicMock(), text=MagicMock())
_make_stub("sqlalchemy.ext", asyncio=MagicMock())
_make_stub("sqlalchemy.ext.asyncio", AsyncSession=MagicMock())
_make_stub("sqlalchemy.orm", Session=MagicMock())
_make_stub("redis")
_make_stub("redis.asyncio", from_url=MagicMock())
_make_stub("app.config", settings=MagicMock(redis_url="redis://localhost"))
_make_stub("app.db.session", get_db=MagicMock(), async_session_factory=MagicMock())
_make_stub("app.dependencies", verify_agent_token=MagicMock())
_make_stub("app.models")
_make_stub("app.models.agent", Agent=MagicMock())

from app.api.telegram_actions import _tg_request  # noqa: E402


# --- Helpers ---

class _FakeResponse:
    def __init__(self, status_code: int, body: bytes, is_json: bool = True):
        self.status_code = status_code
        self._body = body
        self._is_json = is_json

    def json(self):
        if not self._is_json:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return json.loads(self._body)


def _client_ctx(response: _FakeResponse):
    """Build an async context manager mock whose .post() returns *response*."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# --- Tests ---

@pytest.mark.asyncio
async def test_json_decode_error_becomes_502():
    """Non-JSON Telegram response (e.g. Cloudflare HTML) must raise 502, not crash."""
    from fastapi import HTTPException

    html = _FakeResponse(200, b"<html>Bad Gateway</html>", is_json=False)
    with patch("app.api.telegram_actions.httpx.AsyncClient", return_value=_client_ctx(html)):
        with pytest.raises(HTTPException) as exc_info:
            await _tg_request("tok", "sendMessage", {"chat_id": 1, "text": "hi"})
    assert exc_info.value.status_code == 502
    assert "non-JSON" in exc_info.value.detail


@pytest.mark.asyncio
async def test_ok_response_returns_result():
    """Valid Telegram JSON response returns the result payload."""
    ok = _FakeResponse(200, json.dumps({"ok": True, "result": {"message_id": 7}}).encode())
    with patch("app.api.telegram_actions.httpx.AsyncClient", return_value=_client_ctx(ok)):
        result = await _tg_request("tok", "sendMessage", {"chat_id": 1, "text": "hi"})
    assert result == {"message_id": 7}


@pytest.mark.asyncio
async def test_not_ok_becomes_400():
    """Telegram ok=false response raises 400 with the description."""
    from fastapi import HTTPException

    err = _FakeResponse(200, json.dumps({"ok": False, "description": "Bad Request"}).encode())
    with patch("app.api.telegram_actions.httpx.AsyncClient", return_value=_client_ctx(err)):
        with pytest.raises(HTTPException) as exc_info:
            await _tg_request("tok", "sendMessage", {"chat_id": 1, "text": "hi"})
    assert exc_info.value.status_code == 400
    assert "Bad Request" in exc_info.value.detail
