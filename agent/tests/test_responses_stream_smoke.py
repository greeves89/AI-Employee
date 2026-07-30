"""End-to-end smoke test for the Responses API streaming tool-call path (#342).

The `_parse_function_arguments` unit test guards the parser in isolation, but the
arg-stripping regression (#285/#342) actually lives in the *stream loop*: some
Responses-compatible providers emit NO ``response.function_call_arguments.delta``
events and deliver the complete JSON only on the ``.done`` event. If the loop
ignores that final payload, every string argument reaches the server empty. This
test drives a synthetic SSE stream (deltas absent, args only on ``.done``)
through ``_stream_responses`` and asserts the emitted tool_call keeps its args.
"""
import json

import pytest

from app.providers.base import LLMEvent
from app.providers.openai_provider import OpenAIProvider


class _FakeStreamCtx:
    """Minimal async-context stand-in for ``httpx.AsyncClient.stream(...)``."""

    def __init__(self, lines: list[str]):
        self._lines = lines
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return b""


class _FakeHttp:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.is_closed = False

    def stream(self, method, url, json=None, headers=None):  # noqa: A002
        return _FakeStreamCtx(self._lines)


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event)


async def _collect(provider: OpenAIProvider) -> list[LLMEvent]:
    return [ev async for ev in provider._stream_responses("http://x/responses", [], None)]


@pytest.mark.asyncio
async def test_args_only_on_done_event_survive_streaming():
    # The exact #342 shape: function_call added with empty args, NO delta events,
    # complete JSON delivered only on the `.done` event.
    args = {"category": "learning", "key": "lesson", "content": "must survive"}
    lines = [
        _sse({"type": "response.output_item.added",
              "item": {"type": "function_call", "id": "fc_1",
                       "name": "memory_save", "call_id": "call_1"}}),
        _sse({"type": "response.function_call_arguments.done",
              "item_id": "fc_1", "arguments": json.dumps(args)}),
        _sse({"type": "response.completed",
              "response": {"usage": {"input_tokens": 5, "output_tokens": 7}}}),
        "data: [DONE]",
    ]
    provider = OpenAIProvider(
        api_endpoint="http://x", api_key="k", model_name="gpt-5.4-codex",
    )
    provider._http = _FakeHttp(lines)

    events = await _collect(provider)
    tool_calls = [e for e in events if e.type == "tool_call"]

    assert len(tool_calls) == 1
    tc = tool_calls[0]
    assert tc.tool_name == "memory_save"
    assert tc.tool_id == "call_1"
    # The regression: these string values would be dropped, leaving {}.
    assert tc.tool_input == args


@pytest.mark.asyncio
async def test_streamed_deltas_still_work():
    # Providers that DO stream deltas must keep working (args assembled from deltas).
    provider = OpenAIProvider(
        api_endpoint="http://x", api_key="k", model_name="gpt-5.4-codex",
    )
    provider._http = _FakeHttp([
        _sse({"type": "response.output_item.added",
              "item": {"type": "function_call", "id": "fc_2",
                       "name": "rate_task", "call_id": "call_2"}}),
        _sse({"type": "response.function_call_arguments.delta",
              "item_id": "fc_2", "delta": '{"rating":5,'}),
        _sse({"type": "response.function_call_arguments.delta",
              "item_id": "fc_2", "delta": '"note":"ok"}'}),
        _sse({"type": "response.function_call_arguments.done", "item_id": "fc_2"}),
        "data: [DONE]",
    ])

    tool_calls = [e for e in await _collect(provider) if e.type == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_input == {"rating": 5, "note": "ok"}
