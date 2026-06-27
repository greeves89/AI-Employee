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
import io
import json
import logging
import os

from app.providers.base import ChatMessage

logger = logging.getLogger(__name__)

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


def sniff_media_type(data: bytes) -> str | None:
    """Detect the REAL image format from magic bytes (ignores filename/header lies).

    Returns one of the vision-API-supported media types, or None if the bytes are
    not one of those four formats. This is the source of truth — a file named
    ``logo.png`` that actually holds SVG/HTML returns None here, never "image/png".
    """
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _looks_like_svg(data: bytes) -> bool:
    head = data[:512].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096].lower())


def normalize_image(data: bytes) -> tuple[bytes, str] | None:
    """Guarantee vision-API-compatible image bytes.

    Returns ``(bytes, media_type)`` where the media type is always one of the
    supported four AND the bytes really are that format. Conversions:
      - already png/jpeg/gif/webp  → returned unchanged
      - SVG (logos!)               → rasterized to PNG via cairosvg (libcairo2)
      - other raster (bmp/tiff/ico/avif/heic/…) → PNG via Pillow

    Returns ``None`` if the data is not a usable image (HTML error page, corrupt,
    or unconvertible) — callers surface a tool error and the agent continues.
    """
    if not data:
        return None
    real = sniff_media_type(data)
    if real:
        return data, real

    # SVG → PNG. Logos are almost always SVG, so converting (not rejecting) lets
    # the agent actually see them.
    if _looks_like_svg(data):
        try:
            import cairosvg
            png = cairosvg.svg2png(bytestring=data, output_width=1024)
            if png and sniff_media_type(png) == "image/png":
                return png, "image/png"
        except Exception as e:
            logger.warning(f"[image] SVG→PNG conversion failed: {e}")
        return None

    # Any other raster format Pillow can decode → PNG.
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
        if sniff_media_type(png) == "image/png":
            return png, "image/png"
    except Exception as e:
        logger.warning(f"[image] Pillow conversion failed: {e}")
    return None


def safe_image_blocks(images: list[dict] | None) -> list[dict]:
    """Provider-side safety net: keep only image blocks whose base64 bytes REALLY
    are a supported format, correcting the declared media_type to the sniffed one.

    Mismatched/unsupported/corrupt blocks are dropped so a single bad image can
    never 400 an entire completion — no matter which path produced it. The agent
    keeps going instead of the task dying mid-run.
    """
    out: list[dict] = []
    for im in images or []:
        try:
            raw = base64.b64decode(im.get("data", ""), validate=False)
        except Exception:
            logger.warning("[image] dropping block with undecodable base64")
            continue
        real = sniff_media_type(raw)
        if real:
            out.append({**im, "media_type": real})
        else:
            logger.warning(
                f"[image] dropping unsupported image block "
                f"(declared {im.get('media_type', '?')}, bytes are not png/jpeg/gif/webp)"
            )
    return out


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
