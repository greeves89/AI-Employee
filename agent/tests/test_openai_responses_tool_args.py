"""Regression tests for Responses API function-call argument parsing."""

from app.providers.openai_provider import OpenAIProvider


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
