"""Multimodal (vision) support for the custom-LLM runtime.

The hand-built agentic loop passes only text between the model and its
tools. This module adds the missing image path so models that ARE
multimodal (Claude, GPT-4o, Gemini) can actually *see* images — e.g. a
photo the user sent via Telegram, or an image file the agent downloaded.

Two image entry points:

  1. Tool results — the ``view_image`` tool encodes an image as a
     sentinel-prefixed JSON string. ``tool_message`` detects the sentinel
     and turns the result into a list of generic content blocks.
  2. User messages — Telegram photos arrive already base64-encoded in the
     queue payload; ``user_message`` folds them in as image blocks.

Generic block format (provider-agnostic, stored in ``ChatMessage.content``)::

    {"type": "text",  "text": "..."}
    {"type": "image", "media_type": "image/jpeg", "data": "<base64>"}

Each provider converts these blocks to its own API's image representation.
"""

import base64
import json
import os

from app.providers.base import ChatMessage

# Unlikely to occur in normal tool output — marks an image-carrying result.
IMAGE_SENTINEL = "\x00MM_IMG\x00"

# Practical upload ceiling shared by Anthropic / OpenAI / Gemini.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_EXT_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SUPPORTED_MEDIA_TYPES = frozenset(_EXT_MEDIA_TYPES.values())


def guess_media_type(name: str, default: str = "image/jpeg") -> str:
    """Best-effort media type from a filename extension."""
    return _EXT_MEDIA_TYPES.get(os.path.splitext(name)[1].lower(), default)


def encode_image_result(data: bytes, media_type: str, note: str = "") -> str:
    """Encode raw image bytes as a sentinel tool-result string."""
    payload = {
        "media_type": media_type,
        "data": base64.b64encode(data).decode("ascii"),
        "note": note,
    }
    return IMAGE_SENTINEL + json.dumps(payload)


def is_image_result(text: object) -> bool:
    return isinstance(text, str) and text.startswith(IMAGE_SENTINEL)


def _parse_image_result(text: str) -> dict | None:
    try:
        return json.loads(text[len(IMAGE_SENTINEL):])
    except (json.JSONDecodeError, ValueError):
        return None


def parse_image_result(text: str) -> dict | None:
    """Public: decode a sentinel image result to its {media_type,data,note} dict."""
    return _parse_image_result(text)


def image_block(media_type: str, data: str) -> dict:
    return {"type": "image", "media_type": media_type, "data": data}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def tool_message(result_text: str, tool_call_id: str, name: str) -> ChatMessage:
    """Build the ChatMessage for a tool result, image-aware.

    A plain string result yields a normal text tool message. An image
    result (sentinel-prefixed) yields a tool message whose ``content`` is a
    list of generic blocks the providers render as real images.
    """
    if is_image_result(result_text):
        payload = _parse_image_result(result_text)
        if payload and payload.get("data"):
            note = payload.get("note") or "Image attached above."
            blocks = [
                image_block(payload.get("media_type", "image/jpeg"), payload["data"]),
                text_block(note),
            ]
            return ChatMessage(
                role="tool", content=blocks, tool_call_id=tool_call_id, name=name
            )
    return ChatMessage(
        role="tool", content=result_text, tool_call_id=tool_call_id, name=name
    )


def log_summary(result_text: str) -> str:
    """A short, log-safe summary of a tool result (never dumps base64)."""
    if is_image_result(result_text):
        payload = _parse_image_result(result_text) or {}
        approx = len(payload.get("data", "")) * 3 // 4
        return f"[image {payload.get('media_type', '?')}, ~{approx // 1024} KB attached for vision]"
    return result_text[:2000]


def user_message(text: str, images: list[dict] | None) -> ChatMessage:
    """Build a user ChatMessage, attaching images as content blocks if any.

    ``images`` is a list of ``{"media_type": ..., "data": <base64>}`` dicts
    (the shape the orchestrator puts in the Telegram queue payload).
    """
    if not images:
        return ChatMessage(role="user", content=text)
    blocks: list[dict] = [text_block(text)] if text else []
    for img in images:
        data = img.get("data")
        if data:
            blocks.append(image_block(img.get("media_type", "image/jpeg"), data))
    if len(blocks) <= (1 if text else 0):
        return ChatMessage(role="user", content=text)
    return ChatMessage(role="user", content=blocks)


def split_blocks(content: object) -> tuple[str, list[dict]]:
    """Split generic block-list content into (joined_text, image_blocks).

    For string content, returns it as-is with no images. Providers use this
    to render text + images for APIs that need them in separate places.
    """
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return ("" if content is None else str(content)), []
    texts: list[str] = []
    images: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            texts.append(str(block))
        elif block.get("type") == "image":
            images.append(block)
        elif block.get("type") == "text":
            texts.append(block.get("text", ""))
    return "\n".join(t for t in texts if t), images
