"""Regression tests for issue #475: custom-LLM/Codex agents must see and execute
the Desktop Bridge tool instead of being forced toward server-side browser/bash
fallbacks."""

import json

import pytest

from app import codex_runner
from app import multimodal
from app.llm_chat_handler import CORE_TOOL_NAMES, DESKTOP_MCP_ACTIVE_ENV, _core_tool_names
from app.runner_hooks import TASK_STARTUP_PREFIX
from app.tools.api_client import OrchestratorAPIClient
from app.tools.definitions import ORCHESTRATOR_TOOL_NAMES, TOOL_DEFINITIONS


def _tool(name: str) -> dict:
    return next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == name)


def test_computer_use_is_core_orchestrator_tool():
    assert "computer_use" in ORCHESTRATOR_TOOL_NAMES
    assert "computer_use" in CORE_TOOL_NAMES
    assert "computer_use" in _core_tool_names()

    description = _tool("computer_use")["function"]["description"]
    assert "user's real desktop" in description
    assert "server-side browser" in description
    assert "internal/company URLs" in description


def test_task_startup_prefix_contains_computer_use():
    assert "computer_use" in TASK_STARTUP_PREFIX


def test_computer_use_filtered_from_core_when_desktop_mcp_active(monkeypatch):
    monkeypatch.setenv(DESKTOP_MCP_ACTIVE_ENV, "1")

    assert "computer_use" not in _core_tool_names()
    assert "computer_use" in CORE_TOOL_NAMES


def test_computer_use_remains_core_without_desktop_mcp(monkeypatch):
    monkeypatch.delenv(DESKTOP_MCP_ACTIVE_ENV, raising=False)

    assert "computer_use" in _core_tool_names()


@pytest.mark.asyncio
async def test_computer_use_lists_sessions(monkeypatch):
    client = OrchestratorAPIClient()
    calls = []

    async def fake_request(method, path, json=None, params=None):
        calls.append((method, path, json, params))
        return {
            "sessions": [{
                "session_id": "s1",
                "status": "connected",
                "platform": "windows",
                "agent_id": None,
                "allowed_capabilities": ["screenshots", "mouse"],
            }]
        }

    monkeypatch.setattr(client, "_request", fake_request)

    result = await client.computer_use({"action": "list_sessions"})

    assert calls == [("GET", "/computer-use/sessions", None, None)]
    assert "session_id=s1" in result
    assert "status=connected" in result
    assert "screenshots, mouse" in result


@pytest.mark.asyncio
async def test_computer_use_requires_session_for_actions():
    client = OrchestratorAPIClient()

    result = await client.computer_use({"action": "screenshot"})

    assert result == "Error: session_id is required. Call computer_use(action='list_sessions') first."


@pytest.mark.asyncio
async def test_computer_use_sends_command(monkeypatch):
    client = OrchestratorAPIClient()
    calls = []

    async def fake_request(method, path, json=None, params=None):
        calls.append((method, path, json, params))
        return {"result": {"ok": True}}

    monkeypatch.setattr(client, "_request", fake_request)

    result = await client.computer_use({
        "action": "mouse_click",
        "session_id": "s1",
        "params": {"x": 10, "y": 20},
        "timeout": 3,
    })

    assert calls == [(
        "POST",
        "/computer-use/sessions/s1/command",
        {"action": "mouse_click", "params": {"x": 10, "y": 20}, "timeout": 3.0},
        None,
    )]
    assert json.loads(result) == {"ok": True}


@pytest.mark.asyncio
async def test_computer_use_screenshot_returns_presentable_image(monkeypatch):
    client = OrchestratorAPIClient()

    async def fake_request(method, path, json=None, params=None):
        return {"result": {"screenshot_b64": "abc123"}}

    monkeypatch.setattr(client, "_request", fake_request)

    result = await client.computer_use({"action": "screenshot", "session_id": "s1"})

    assert result.startswith(multimodal.IMAGE_SENTINEL)
    payload = multimodal.parse_image_result(result)
    assert payload == {
        "media_type": "image/png",
        "data": "abc123",
        "note": "Screenshot captured from the user's desktop.",
    }


@pytest.mark.asyncio
async def test_computer_use_screenshot_without_image_is_error(monkeypatch):
    client = OrchestratorAPIClient()

    async def fake_request(method, path, json=None, params=None):
        return {"result": {"ok": False, "error": "screenshots disabled"}}

    monkeypatch.setattr(client, "_request", fake_request)

    result = await client.computer_use({"action": "screenshot", "session_id": "s1"})

    assert result == "Error: screenshot did not return image data"
    assert not result.startswith(multimodal.IMAGE_SENTINEL)


def test_codex_config_includes_desktop_mcp(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_runner.os.path, "exists", lambda path: True)
    env = {
        "ORCHESTRATOR_URL": "http://orchestrator:8000",
        "AGENT_ID": "agent-1",
        "AGENT_TOKEN": "token",
    }

    desktop_mcp_active = codex_runner._ensure_codex_mcp_config(str(tmp_path), env)

    config = (tmp_path / "config.toml").read_text()
    assert desktop_mcp_active is True
    assert "[mcp_servers.desktop]" in config
    assert 'args = ["/opt/mcp/computer-use-server.mjs"]' in config
    assert 'AGENT_ID = "agent-1"' in config


def test_codex_env_marks_desktop_mcp_active(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
    monkeypatch.setenv("AGENT_ID", "agent-1")
    monkeypatch.setenv("AGENT_TOKEN", "token")
    monkeypatch.setattr(codex_runner.os.path, "exists", lambda path: path.endswith("computer-use-server.mjs"))

    env = codex_runner._codex_env()

    assert env[DESKTOP_MCP_ACTIVE_ENV] == "1"
