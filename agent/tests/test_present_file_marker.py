"""Tests for present_file marker parsing in AgentRunner (issue #180)."""

from app.agent_runner import AgentRunner


def test_parse_present_file_marker_from_string():
    raw = '__AI_EMPLOYEE_PRESENT_FILE__{"path": "/workspace/transfer/x.mp3", "filename": "x.mp3", "media_type": "audio/mpeg", "size": 1024, "caption": "hi"}'
    payload = AgentRunner._parse_present_file_marker(raw)
    assert payload is not None
    assert payload["path"] == "/workspace/transfer/x.mp3"
    assert payload["filename"] == "x.mp3"
    assert payload["media_type"] == "audio/mpeg"
    assert payload["size"] == 1024
    assert payload["caption"] == "hi"


def test_parse_present_file_marker_from_blocks():
    content = [
        {"type": "text", "text": '__AI_EMPLOYEE_PRESENT_FILE__{"path": "/p/a.pdf", "filename": "a.pdf"}'},
    ]
    payload = AgentRunner._parse_present_file_marker(content)
    assert payload is not None
    assert payload["path"] == "/p/a.pdf"


def test_parse_present_file_marker_missing_returns_none():
    assert AgentRunner._parse_present_file_marker("just a regular tool result") is None
    assert AgentRunner._parse_present_file_marker(None) is None
    assert AgentRunner._parse_present_file_marker([]) is None
    assert AgentRunner._parse_present_file_marker([{"type": "text", "text": "no marker"}]) is None


def test_parse_present_file_marker_invalid_json_returns_none():
    assert AgentRunner._parse_present_file_marker("__AI_EMPLOYEE_PRESENT_FILE__{broken json}") is None
