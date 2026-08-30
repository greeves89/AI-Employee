"""Regression tests for Responses API function-call argument parsing."""

from app.providers.base import ChatMessage
from app.providers.openai_provider import OpenAIProvider


def _provider(model_name="gpt-5.6-luna"):
    return OpenAIProvider(
        api_endpoint="https://example.invalid", api_key="k", model_name=model_name,
    )


def test_function_arguments_fall_back_to_done_payload():
    parsed = OpenAIProvider._parse_function_arguments(
        "",
        '{"category":"learning","key":"lesson_learned","content":"kept"}',
    )

    assert parsed == {
        "category": "learning",
        "key": "lesson_learned",
        "content": "kept",
    }


def test_streamed_function_arguments_win_over_done_payload():
    parsed = OpenAIProvider._parse_function_arguments(
        '{"rating":5,"reflection":"streamed"}',
        '{"rating":3,"reflection":"final"}',
    )

    assert parsed == {"rating": 5, "reflection": "streamed"}


def test_invalid_function_arguments_are_preserved_as_raw():
    parsed = OpenAIProvider._parse_function_arguments("", '{"category":')

    assert parsed == {"raw": '{"category":'}


def test_a_malformed_tool_call_entry_does_not_crash_the_whole_turn():
    """Beim Kunden gemeldet: 'tuple' object has no attribute 'get' (2026-08-28,
    Kundenanlage). ChatMessage.tool_calls is an untyped list — a
    non-dict entry must be skipped, not crash body-building for the whole
    Responses-API request before the provider's own try/except even starts."""
    provider = _provider()
    messages = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ("not", "a", "dict"),
                {"id": "call_1", "function": {"name": "web_search", "arguments": "{}"}},
            ],
        ),
    ]

    body = provider._build_responses_body(messages, tools=None)

    calls = [item for item in body["input"] if item.get("type") == "function_call"]
    assert len(calls) == 1
    assert calls[0]["name"] == "web_search"
    assert calls[0]["call_id"] == "call_1"
