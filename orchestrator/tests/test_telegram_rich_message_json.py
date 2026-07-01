"""Tests that /telegram/send-rich-message forwards rich_message as a nested
JSON object — not a JSON-stringified blob.

The orchestrator's `_tg_request` uses `httpx.post(url, json=data)` with
Content-Type: application/json. Telegram then expects nested fields like
`rich_message` and `reply_markup` to be real JSON objects. Double-encoding
them via json.dumps() produces opaque strings that Telegram rejects with
"rich message must be non-empty".
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.telegram_actions import (
    SendRichMessageRequest,
    send_rich_message,
    send_rich_message_draft,
)


@pytest.mark.asyncio
async def test_rich_message_is_forwarded_as_dict_not_string():
    body = SendRichMessageRequest(
        chat_id=480455764,
        markdown="## Title\n\nBody paragraph",
        reply_markup={"inline_keyboard": [[{"text": "ok", "callback_data": "ok"}]]},
    )

    captured: dict = {}

    async def fake_tg_request(token, method, data=None, files=None, timeout=30):
        captured["method"] = method
        captured["data"] = data
        return {"message_id": 1}

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

    assert captured["method"] == "sendRichMessage"
    rm = captured["data"]["rich_message"]
    assert isinstance(rm, dict), f"rich_message must be a dict, got {type(rm).__name__}"
    assert "markdown" in rm
    rk = captured["data"]["reply_markup"]
    assert isinstance(rk, dict), f"reply_markup must be a dict, got {type(rk).__name__}"
    assert captured["data"]["chat_id"] == str(480455764)


@pytest.mark.asyncio
async def test_draft_endpoint_also_forwards_as_dict():
    body = SendRichMessageRequest(
        chat_id=480455764,
        html="<b>draft chunk</b>",
    )

    captured: dict = {}

    async def fake_tg_request(token, method, data=None, files=None, timeout=30):
        captured["method"] = method
        captured["data"] = data
        return {}

    with patch(
        "app.api.telegram_actions._get_bot_token",
        new=AsyncMock(return_value="bot:token"),
    ), patch(
        "app.api.telegram_actions._tg_request",
        new=fake_tg_request,
    ):
        await send_rich_message_draft(
            body=body,
            agent_auth={"agent_id": "67874999"},
            db=AsyncMock(),
        )

    assert captured["method"] == "sendRichMessageDraft"
    rm = captured["data"]["rich_message"]
    assert isinstance(rm, dict), f"rich_message must be a dict, got {type(rm).__name__}"
    assert "html" in rm
