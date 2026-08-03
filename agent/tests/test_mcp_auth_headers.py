"""Regression tests for issue #424: bearer tokens from CUSTOM_MCP_AUTH (and custom
headers from CUSTOM_MCP_HEADERS) must be passed to `claude mcp add` and into the
.mcp.json fallback, otherwise authenticated MCP servers register without credentials
and are unusable."""
import json

from app import main


def test_auth_headers_for_builds_bearer():
    hdrs = main._auth_headers_for("composio", {"composio": "tok123"}, {})
    assert hdrs == {"Authorization": "Bearer tok123"}


def test_auth_headers_for_merges_custom_headers():
    hdrs = main._auth_headers_for(
        "srv", {"srv": "t"}, {"srv": {"X-Api-Key": "k", "empty": None}}
    )
    assert hdrs == {"Authorization": "Bearer t", "X-Api-Key": "k"}


def test_auth_headers_for_no_credentials():
    assert main._auth_headers_for("srv", {}, {}) == {}


def test_auth_header_args_format():
    args = main._auth_header_args("srv", {"srv": "t"}, {"srv": {"X-Api-Key": "k"}})
    assert "Authorization: Bearer t" in args
    assert "X-Api-Key: k" in args


def test_register_passes_header_to_claude_mcp_add(monkeypatch):
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"composio": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"composio": "tok123"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)
    # Neutralise the other registration paths so only the custom loop runs.
    monkeypatch.delenv("COMPUTER_USE_BRIDGE_MCP_URL", raising=False)

    calls = []
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: calls.append(args) or True)
    monkeypatch.setattr(main, "_write_mcp_json_fallback", lambda: None)
    # Skip built-in stdio + msgraph registration side effects.
    monkeypatch.setattr(main, "_sanitize_mcp_name", lambda n: n)

    main.register_mcp_servers()

    custom_calls = [c for c in calls if "https://x/mcp" in c]
    assert len(custom_calls) == 1
    args = custom_calls[0]
    assert "--header" in args
    assert "Authorization: Bearer tok123" in args


def test_register_uses_original_name_for_auth_but_sanitized_for_add(monkeypatch):
    """Auth is keyed by the ORIGINAL server name, while the server is registered
    under the sanitized name — a mismatch must not drop the credential (W2 / #430)."""
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"My Server": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"My Server": "tok123"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)
    monkeypatch.delenv("COMPUTER_USE_BRIDGE_MCP_URL", raising=False)

    calls = []
    monkeypatch.setattr(main, "_run_mcp_add", lambda args: calls.append(args) or True)
    monkeypatch.setattr(main, "_write_mcp_json_fallback", lambda: None)
    monkeypatch.setattr(main, "_sanitize_mcp_name", lambda n: n.replace(" ", "-"))

    main.register_mcp_servers()

    custom_calls = [c for c in calls if "https://x/mcp" in c]
    assert len(custom_calls) == 1
    args = custom_calls[0]
    # Registered under the sanitized name...
    assert "My-Server" in args
    assert "My Server" not in args
    # ...but the credential (looked up by original name) is still present.
    assert "Authorization: Bearer tok123" in args


def test_mcp_json_fallback_includes_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_MCP_SERVERS", json.dumps({"composio": "https://x/mcp"}))
    monkeypatch.setenv("CUSTOM_MCP_AUTH", json.dumps({"composio": "tok123"}))
    monkeypatch.delenv("CUSTOM_MCP_HEADERS", raising=False)
    monkeypatch.setattr(main.settings, "workspace_dir", str(tmp_path))

    main._write_mcp_json_fallback()

    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    entry = cfg["mcpServers"]["composio"]
    assert entry["url"] == "https://x/mcp"
    assert entry["headers"] == {"Authorization": "Bearer tok123"}
