"""Tests for present_file marker parsing in scheduler/task runs."""

import base64
import json

import pytest

from app.agent_runner import AgentRunner


def test_parse_present_file_marker_from_string():
    raw = (
        "__AI_EMPLOYEE_PRESENT_FILE__"
        '{"path": "/workspace/transfer/x.mp3", "filename": "x.mp3", '
        '"media_type": "audio/mpeg", "size": 1024, "caption": "hi"}'
    )
    payload = AgentRunner._parse_present_file_marker(raw)
    assert payload is not None
    assert payload["path"] == "/workspace/transfer/x.mp3"
    assert payload["filename"] == "x.mp3"
    assert payload["media_type"] == "audio/mpeg"
    assert payload["size"] == 1024
    assert payload["caption"] == "hi"


def test_parse_present_file_marker_from_blocks():
    content = [
        {
            "type": "text",
            "text": '__AI_EMPLOYEE_PRESENT_FILE__{"path": "/p/a.pdf", "filename": "a.pdf"}',
        },
    ]
    payload = AgentRunner._parse_present_file_marker(content)
    assert payload is not None
    assert payload["path"] == "/p/a.pdf"


def test_present_file_payloads_from_user_tool_result_event():
    event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "__AI_EMPLOYEE_PRESENT_FILE__"
                                '{"path": "/workspace/transfer/podcast.mp3", "filename": "podcast.mp3"}'
                            ),
                        }
                    ],
                }
            ]
        },
    }
    runner = AgentRunner(log_publisher=None)
    payloads = runner._present_file_payloads_from_event(event)
    assert len(payloads) == 1
    assert payloads[0]["filename"] == "podcast.mp3"


def test_parse_present_file_marker_missing_returns_none():
    assert AgentRunner._parse_present_file_marker("just a regular tool result") is None
    assert AgentRunner._parse_present_file_marker(None) is None
    assert AgentRunner._parse_present_file_marker([]) is None
    assert AgentRunner._parse_present_file_marker([{"type": "text", "text": "no marker"}]) is None


def test_parse_present_file_marker_invalid_json_returns_none():
    assert AgentRunner._parse_present_file_marker("__AI_EMPLOYEE_PRESENT_FILE__{broken json}") is None


class _FakeRedis:
    def __init__(self):
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel, message):
        self.published.append((channel, message))


class _FakeLogPublisher:
    def __init__(self, redis, agent_id):
        self.redis = redis
        self.agent_id = agent_id


@pytest.mark.anyio
async def test_present_file_delivered_via_telegram(tmp_path):
    f = tmp_path / "podcast.mp3"
    f.write_bytes(b"ID3 fake audio bytes")
    redis = _FakeRedis()
    runner = AgentRunner(log_publisher=_FakeLogPublisher(redis, "agent-1"))

    await runner._deliver_present_file_via_telegram(
        {
            "path": str(f),
            "filename": "podcast.mp3",
            "media_type": "audio/mpeg",
            "caption": "Morgen-Podcast",
        }
    )

    assert len(redis.published) == 1
    channel, raw = redis.published[0]
    assert channel == "agent:agent-1:telegram:send"
    payload = json.loads(raw)
    assert payload["filename"] == "podcast.mp3"
    assert payload["media_type"] == "audio/mpeg"
    assert payload["caption"] == "Morgen-Podcast"
    assert base64.b64decode(payload["file_b64"]) == b"ID3 fake audio bytes"


@pytest.mark.anyio
async def test_telegram_fallback_on_present_file_failure(tmp_path):
    """A missing or oversized file must not raise and must not publish."""
    redis = _FakeRedis()
    runner = AgentRunner(log_publisher=_FakeLogPublisher(redis, "agent-1"))

    # Missing file → silent no-op
    await runner._deliver_present_file_via_telegram({"path": str(tmp_path / "gone.mp3")})
    assert redis.published == []

    # Oversized file (> 20 MB) → skipped, chat mirror remains the only path
    big = tmp_path / "big.bin"
    big.write_bytes(b"\0" * (20 * 1024 * 1024 + 1))
    await runner._deliver_present_file_via_telegram({"path": str(big)})
    assert redis.published == []
