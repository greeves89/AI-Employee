"""Telegram Bot API actions — called by agents to manage their own Telegram bot.

Agents can:
- Send rich messages (text, voice, photo, document, video, sticker)
- Set bot commands, description, about text
- Send inline keyboards / reply keyboards
- Pin/unpin messages
- Get bot info
"""

import base64
import json
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import redis.asyncio as aioredis
from app.config import settings
from app.dependencies import verify_agent_token
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.agent import Agent

router = APIRouter(prefix="/telegram", tags=["telegram-actions"])

TG_API = "https://api.telegram.org/bot{token}"


async def _get_bot_token(agent_id: str, db: AsyncSession) -> str:
    """Get the Telegram bot token for an agent."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    config = agent.config or {}
    token = config.get("telegram_bot_token")
    if not token:
        raise HTTPException(status_code=400, detail="No Telegram bot configured for this agent")
    return token


async def _tg_request(token: str, method: str, data: dict | None = None, files: dict | None = None) -> dict:
    """Make a request to the Telegram Bot API."""
    url = f"{TG_API.format(token=token)}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
        if files:
            resp = await client.post(url, data=data or {}, files=files)
        else:
            resp = await client.post(url, json=data or {})
        result = resp.json()
        if not result.get("ok"):
            raise HTTPException(
                status_code=400,
                detail=f"Telegram API error: {result.get('description', 'Unknown error')}",
            )
        return result.get("result", {})


# --- Models ---


class BroadcastRequest(BaseModel):
    text: str | None = None
    message: str | None = None  # Alias — agent may send "message" instead of "text"
    parse_mode: str | None = None

    @property
    def resolved_text(self) -> str:
        return self.text or self.message or ""


class SendMessageRequest(BaseModel):
    chat_id: int | str
    text: str
    parse_mode: str | None = None
    reply_markup: dict | None = None
    reply_to_message_id: int | None = None
    disable_notification: bool = False


class SendVoiceRequest(BaseModel):
    chat_id: int | str
    voice_base64: str  # base64-encoded OGG/OPUS audio
    caption: str | None = None
    parse_mode: str | None = None
    duration: int | None = None
    reply_markup: dict | None = None


class SendPhotoRequest(BaseModel):
    chat_id: int | str
    photo_base64: str | None = None  # base64-encoded image
    photo_url: str | None = None  # OR a URL
    caption: str | None = None
    parse_mode: str | None = None
    reply_markup: dict | None = None


class SendDocumentRequest(BaseModel):
    chat_id: int | str
    document_base64: str  # base64-encoded file
    filename: str = "file"
    caption: str | None = None
    parse_mode: str | None = None
    reply_markup: dict | None = None


class SendVideoRequest(BaseModel):
    chat_id: int | str
    video_base64: str | None = None
    video_url: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    duration: int | None = None
    reply_markup: dict | None = None


class SendStickerRequest(BaseModel):
    chat_id: int | str
    sticker: str  # file_id or URL


class SendLocationRequest(BaseModel):
    chat_id: int | str
    latitude: float
    longitude: float
    reply_markup: dict | None = None


class EditMessageRequest(BaseModel):
    chat_id: int | str
    message_id: int
    text: str
    parse_mode: str | None = None
    reply_markup: dict | None = None


class SetCommandsRequest(BaseModel):
    commands: list[dict]  # [{"command": "start", "description": "Start"}]
    scope: dict | None = None  # BotCommandScope
    language_code: str | None = None


class SetDescriptionRequest(BaseModel):
    description: str | None = None
    short_description: str | None = None


class PinMessageRequest(BaseModel):
    chat_id: int | str
    message_id: int
    disable_notification: bool = False


class SendChatActionRequest(BaseModel):
    chat_id: int | str
    action: str = "typing"  # typing, upload_photo, record_voice, upload_voice, upload_document, record_video, upload_video


class AnswerCallbackRequest(BaseModel):
    callback_query_id: str
    text: str | None = None
    show_alert: bool = False


class SendAnimationRequest(BaseModel):
    chat_id: int | str
    animation_base64: str | None = None
    animation_url: str | None = None
    caption: str | None = None
    parse_mode: str | None = None
    reply_markup: dict | None = None


# --- Endpoints ---


@router.get("/info")
async def get_bot_info(
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Get info about the agent's Telegram bot."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    return await _tg_request(token, "getMe")


@router.post("/broadcast")
async def broadcast(
    body: BroadcastRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Broadcast a text message to ALL authorized Telegram users of this agent.

    Use this when the agent doesn't have a specific chat_id (e.g. proactive tasks).
    """
    agent_id = agent_auth["agent_id"]
    text = body.resolved_text
    if not text:
        raise HTTPException(status_code=422, detail="text or message field required")

    token = await _get_bot_token(agent_id, db)
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        chat_ids = await r.smembers(f"agent:{agent_id}:tg_auth")
    finally:
        await r.aclose()
    if not chat_ids:
        return {"sent": 0, "detail": "No authorized Telegram users"}

    sent = 0
    for cid in chat_ids:
        try:
            data = {"chat_id": int(cid), "text": text}
            if body.parse_mode:
                data["parse_mode"] = body.parse_mode
            await _tg_request(token, "sendMessage", data)
            sent += 1
        except Exception:
            pass
    return {"sent": sent, "total_users": len(chat_ids)}


@router.post("/send-message")
async def send_message(
    body: SendMessageRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a text message to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {"chat_id": body.chat_id, "text": body.text}
    if body.parse_mode:
        data["parse_mode"] = body.parse_mode
    if body.reply_markup:
        data["reply_markup"] = body.reply_markup
    if body.reply_to_message_id:
        data["reply_to_message_id"] = body.reply_to_message_id
    if body.disable_notification:
        data["disable_notification"] = True
    return await _tg_request(token, "sendMessage", data)


@router.post("/send-voice")
async def send_voice(
    body: SendVoiceRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a voice message (OGG/OPUS) to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    audio_bytes = base64.b64decode(body.voice_base64)
    data = {"chat_id": str(body.chat_id)}
    if body.caption:
        data["caption"] = body.caption
    if body.parse_mode:
        data["parse_mode"] = body.parse_mode
    if body.duration:
        data["duration"] = str(body.duration)
    if body.reply_markup:
        data["reply_markup"] = json.dumps(body.reply_markup)
    files = {"voice": ("voice.ogg", audio_bytes, "audio/ogg")}
    return await _tg_request(token, "sendVoice", data, files=files)


@router.post("/send-photo")
async def send_photo(
    body: SendPhotoRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a photo to a Telegram chat (base64 or URL)."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    if body.photo_url:
        data = {"chat_id": body.chat_id, "photo": body.photo_url}
        if body.caption:
            data["caption"] = body.caption
        if body.parse_mode:
            data["parse_mode"] = body.parse_mode
        if body.reply_markup:
            data["reply_markup"] = body.reply_markup
        return await _tg_request(token, "sendPhoto", data)
    elif body.photo_base64:
        photo_bytes = base64.b64decode(body.photo_base64)
        data = {"chat_id": str(body.chat_id)}
        if body.caption:
            data["caption"] = body.caption
        if body.parse_mode:
            data["parse_mode"] = body.parse_mode
        if body.reply_markup:
            data["reply_markup"] = json.dumps(body.reply_markup)
        files = {"photo": ("photo.jpg", photo_bytes, "image/jpeg")}
        return await _tg_request(token, "sendPhoto", data, files=files)
    else:
        raise HTTPException(status_code=400, detail="Provide photo_base64 or photo_url")


@router.post("/send-document")
async def send_document(
    body: SendDocumentRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a document/file to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    doc_bytes = base64.b64decode(body.document_base64)
    data = {"chat_id": str(body.chat_id)}
    if body.caption:
        data["caption"] = body.caption
    if body.parse_mode:
        data["parse_mode"] = body.parse_mode
    if body.reply_markup:
        data["reply_markup"] = json.dumps(body.reply_markup)
    files = {"document": (body.filename, doc_bytes, "application/octet-stream")}
    return await _tg_request(token, "sendDocument", data, files=files)


@router.post("/send-video")
async def send_video(
    body: SendVideoRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a video to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    if body.video_url:
        data = {"chat_id": body.chat_id, "video": body.video_url}
        if body.caption:
            data["caption"] = body.caption
        if body.parse_mode:
            data["parse_mode"] = body.parse_mode
        if body.reply_markup:
            data["reply_markup"] = body.reply_markup
        return await _tg_request(token, "sendVideo", data)
    elif body.video_base64:
        video_bytes = base64.b64decode(body.video_base64)
        data = {"chat_id": str(body.chat_id)}
        if body.caption:
            data["caption"] = body.caption
        if body.parse_mode:
            data["parse_mode"] = body.parse_mode
        if body.reply_markup:
            data["reply_markup"] = json.dumps(body.reply_markup)
        files = {"video": ("video.mp4", video_bytes, "video/mp4")}
        return await _tg_request(token, "sendVideo", data, files=files)
    else:
        raise HTTPException(status_code=400, detail="Provide video_base64 or video_url")


@router.post("/send-sticker")
async def send_sticker(
    body: SendStickerRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a sticker to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {"chat_id": body.chat_id, "sticker": body.sticker}
    return await _tg_request(token, "sendSticker", data)


@router.post("/send-location")
async def send_location(
    body: SendLocationRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a location to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {
        "chat_id": body.chat_id,
        "latitude": body.latitude,
        "longitude": body.longitude,
    }
    if body.reply_markup:
        data["reply_markup"] = body.reply_markup
    return await _tg_request(token, "sendLocation", data)


@router.post("/send-animation")
async def send_animation(
    body: SendAnimationRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a GIF/animation to a Telegram chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    if body.animation_url:
        data = {"chat_id": body.chat_id, "animation": body.animation_url}
        if body.caption:
            data["caption"] = body.caption
        if body.parse_mode:
            data["parse_mode"] = body.parse_mode
        if body.reply_markup:
            data["reply_markup"] = body.reply_markup
        return await _tg_request(token, "sendAnimation", data)
    elif body.animation_base64:
        anim_bytes = base64.b64decode(body.animation_base64)
        data = {"chat_id": str(body.chat_id)}
        if body.caption:
            data["caption"] = body.caption
        if body.parse_mode:
            data["parse_mode"] = body.parse_mode
        if body.reply_markup:
            data["reply_markup"] = json.dumps(body.reply_markup)
        files = {"animation": ("animation.gif", anim_bytes, "image/gif")}
        return await _tg_request(token, "sendAnimation", data, files=files)
    else:
        raise HTTPException(status_code=400, detail="Provide animation_base64 or animation_url")


@router.post("/send-chat-action")
async def send_chat_action(
    body: SendChatActionRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Send a chat action (typing indicator, etc.)."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {"chat_id": body.chat_id, "action": body.action}
    return await _tg_request(token, "sendChatAction", data)


@router.post("/edit-message")
async def edit_message(
    body: EditMessageRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Edit an existing message."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {
        "chat_id": body.chat_id,
        "message_id": body.message_id,
        "text": body.text,
    }
    if body.parse_mode:
        data["parse_mode"] = body.parse_mode
    if body.reply_markup:
        data["reply_markup"] = body.reply_markup
    return await _tg_request(token, "editMessageText", data)


@router.post("/set-commands")
async def set_commands(
    body: SetCommandsRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Set the bot's command menu."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {"commands": body.commands}
    if body.scope:
        data["scope"] = body.scope
    if body.language_code:
        data["language_code"] = body.language_code
    return await _tg_request(token, "setMyCommands", data)


@router.post("/delete-commands")
async def delete_commands(
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Delete the bot's command menu."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    return await _tg_request(token, "deleteMyCommands")


@router.post("/set-description")
async def set_description(
    body: SetDescriptionRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Set the bot's description and/or short description (about text)."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    results = {}
    if body.description is not None:
        results["description"] = await _tg_request(
            token, "setMyDescription", {"description": body.description}
        )
    if body.short_description is not None:
        results["short_description"] = await _tg_request(
            token, "setMyShortDescription", {"short_description": body.short_description}
        )
    return results


@router.post("/pin-message")
async def pin_message(
    body: PinMessageRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Pin a message in a chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {
        "chat_id": body.chat_id,
        "message_id": body.message_id,
        "disable_notification": body.disable_notification,
    }
    return await _tg_request(token, "pinChatMessage", data)


@router.post("/unpin-message")
async def unpin_message(
    body: PinMessageRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Unpin a message in a chat."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {"chat_id": body.chat_id, "message_id": body.message_id}
    return await _tg_request(token, "unpinChatMessage", data)


@router.post("/answer-callback")
async def answer_callback(
    body: AnswerCallbackRequest,
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Answer a callback query from an inline keyboard button."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    data = {
        "callback_query_id": body.callback_query_id,
        "show_alert": body.show_alert,
    }
    if body.text:
        data["text"] = body.text
    return await _tg_request(token, "answerCallbackQuery", data)


@router.get("/get-commands")
async def get_commands(
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Get the bot's current command list."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    return await _tg_request(token, "getMyCommands")


@router.get("/get-description")
async def get_description(
    agent_auth: dict = Depends(verify_agent_token),
    db: AsyncSession = Depends(get_db),
):
    """Get the bot's description and short description."""
    token = await _get_bot_token(agent_auth["agent_id"], db)
    desc = await _tg_request(token, "getMyDescription")
    short_desc = await _tg_request(token, "getMyShortDescription")
    return {"description": desc, "short_description": short_desc}
