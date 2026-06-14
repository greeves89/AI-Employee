"""Tests for the /telegram/send-rich-message endpoint.

Covers the Bot API 10.1 (June 2026) `sendRichMessage` passthrough that lets
agents emit block-structured messages (headings, tables, checklists, math,
inline media) instead of being capped at `parse_mode=HTML`.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.telegram_actions import SendRichMessageRequest, send_rich_message


@pytest.mark.asyncio
async def test_send_rich_message_forwards_blocks_verbatim():
    body = SendRichMessageRequest(
        chat_id=480455764,
        rich_message={
            "blocks": [
                {"type": "section_heading", "text": "Final Exams 2026"},
                {
                    "type": "paragraph",
                    "text": "Everything you need to know.",
                },
                {
                    "type": "table",
                    "rows": [
                        ["Monday", "Tuesday", "Wednesday"],
                        ["Review", "Exam 1", "Review"],
                    ],
                },
            ]
        },
    )

    captured: dict = {}

    async def fake_tg_request(token, method, data=None, files=None):
        captured["token"] = token
        captured["method"] = method
        captured["data"] = data
        return {"message_id": 999, "chat": {"id": 480455764}}

    with patch(
        "app.api.telegram_actions._get_bot_token",
        new=AsyncMock(return_value="bot:token"),
    ), patch(
        "app.api.telegram_actions._tg_request",
        new=fake_tg_request,
    ):
        result = await send_rich_message(
            body=body,
            agent_auth={"agent_id": "67874999"},
            db=AsyncMock(),
        )

    assert captured["method"] == "sendRichMessage"
    assert captured["data"]["chat_id"] == 480455764
    assert captured["data"]["rich_message"]["blocks"][0]["type"] == "section_heading"
    assert len(captured["data"]["rich_message"]["blocks"][2]["rows"]) == 2
    # Optional fields stay out when not set — keeps the payload minimal.
    assert "business_connection_id" not in captured["data"]
    assert "message_thread_id" not in captured["data"]
    assert "reply_parameters" not in captured["data"]
    assert "reply_markup" not in captured["data"]
    assert "disable_notification" not in captured["data"]
    assert result["message_id"] == 999


@pytest.mark.asyncio
async def test_send_rich_message_includes_optional_fields():
    body = SendRichMessageRequest(
        chat_id="@somechan",
        rich_message={"blocks": [{"type": "paragraph", "text": "hi"}]},
        business_connection_id="bc-1",
        message_thread_id=42,
        reply_parameters={"message_id": 17},
        reply_markup={"inline_keyboard": [[{"text": "ok", "callback_data": "ok"}]]},
        disable_notification=True,
    )

    captured: dict = {}

    async def fake_tg_request(token, method, data=None, files=None):
        captured["data"] = data
        return {}

    with patch(
        "app.api.telegram_actions._get_bot_token",
        new=AsyncMock(return_value="bot:token"),
    ), patch(
        "app.api.telegram_actions._tg_request",
        new=fake_tg_request,
    ):
        await send_rich_message(
            body=body,
            agent_auth={"agent_id": "67874999"},
            db=AsyncMock(),
        )

    assert captured["data"]["business_connection_id"] == "bc-1"
    assert captured["data"]["message_thread_id"] == 42
    assert captured["data"]["reply_parameters"] == {"message_id": 17}
    assert captured["data"]["reply_markup"]["inline_keyboard"][0][0]["text"] == "ok"
    assert captured["data"]["disable_notification"] is True


@pytest.mark.asyncio
async def test_send_rich_message_accepts_string_chat_id():
    """Channel usernames (@channel) are valid chat_ids — must not be coerced."""
    body = SendRichMessageRequest(
        chat_id="@mychannel",
        rich_message={"blocks": []},
    )
    assert isinstance(body.chat_id, str)
    assert body.chat_id == "@mychannel"
