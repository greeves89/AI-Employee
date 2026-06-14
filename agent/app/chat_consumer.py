"""Chat consumer - listens for chat messages and forwards them to ChatHandler."""

import asyncio
import json
import logging

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher

logger = logging.getLogger(__name__)

DEFAULT_CHAT_TURN_TIMEOUT = 600  # seconds
DEFAULT_CODEX_CHAT_TURN_TIMEOUT = 1800  # seconds


def _chat_turn_timeout() -> int:
    """Return the watchdog timeout for one chat turn.

    A single chat turn must never block the queue indefinitely. Codex CLI gets
    a longer default because `codex exec` performs its own internal tool loop
    before returning a final result.
    """
    if settings.agent_mode == "codex_cli":
        codex_timeout = int(settings.codex_chat_turn_timeout_seconds or 0)
        if codex_timeout > 0:
            return codex_timeout
        return max(
            int(settings.chat_turn_timeout_seconds or DEFAULT_CHAT_TURN_TIMEOUT),
            DEFAULT_CODEX_CHAT_TURN_TIMEOUT,
        )
    return int(settings.chat_turn_timeout_seconds or DEFAULT_CHAT_TURN_TIMEOUT)


def _build_telegram_prompt(text: str, tg: dict, is_new_session: bool = False) -> str:
    """Wrap the user message with Telegram context and API instructions."""
    chat_id = tg.get("chat_id", "")
    username = tg.get("username", "")
    first_name = tg.get("first_name", "")
    media_type = tg.get("media_type", "")
    file_id = tg.get("file_id", "")
    callback_data = tg.get("callback_data", "")
    callback_query_id = tg.get("callback_query_id", "")

    orch_url = settings.orchestrator_url
    agent_id = settings.agent_id
    agent_token = settings.agent_token

    # Build header
    header = f"[TELEGRAM] From: {first_name or username or 'User'} | chat_id: {chat_id}"
    if media_type:
        header += f" | media: {media_type} (file_id: {file_id})"
    if callback_data:
        header += f" | callback: {callback_data} (query_id: {callback_query_id})"

    # Voice-first: a spoken message gets a spoken reply (auto-TTS). Tell the
    # agent to answer like a colleague on the phone — short, plain, no Markdown.
    voice_hint = ""
    if media_type in ("voice", "audio"):
        voice_hint = (
            "\n\nVOICE CONVERSATION: The user spoke to you and your reply will be "
            "read aloud as a voice message. Answer CONCISELY and conversationally, "
            "like a colleague on the phone — short sentences, no Markdown, no code "
            "blocks, no bullet lists, no tables. Get to the point.\n"
        )

    api_base = f"{orch_url}/api/v1/telegram"
    auth = f"-H 'X-Agent-ID: {agent_id}' -H 'Authorization: Bearer {agent_token}'"

    # Session startup instructions — read knowledge + memories FIRST
    startup_block = ""
    if is_new_session:
        startup_block = """
FIRST STEPS (do these BEFORE responding to the user):
1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns
2. Use brain_search (query relevant to this message) to check the shared knowledge base
3. Use memory_search with a focused query and room="chat:telegram" (or the project-room
   if the user is asking about a specific project). Room filters improve precision massively.
4. Use skill_search to check the marketplace for a skill that fits this request. If one
   fits, skill_install it and FOLLOW its instructions instead of improvising.
5. Use list_todos to check for pending work items
Then respond to the user's message below with full context.

AFTER the user gives feedback on your result: if you used a marketplace skill, call
skill_rate (skill_id, helpfulness 1-5, rating 1-5, and user_rating interpreted from the
user's words: 'super/perfekt'=5 … 'schlecht'=1). Omit task_id — this is a chat.

"""
    else:
        startup_block = """
BEFORE responding: use brain_search and memory_search (with a room filter if you know
the project/area — e.g. room="chat:telegram" or "project:<name>/<area>") to check for
relevant context. Also use skill_search — if a marketplace skill fits this request,
skill_install it and follow it instead of improvising.
AFTER responding: if you learned something new, use memory_save with:
  - category: 'learning'
  - room: "chat:telegram" (or a project room if the insight is project-specific)
  - tag_type: 'permanent' for lessons, 'transient' for in-progress state
  - tags: pick from task/code/decision/learning/error/correction/pattern/architecture/
          performance/security/user_preference/meta
AFTER the user gives feedback: if you used a marketplace skill, call skill_rate
(skill_id, helpfulness, rating, user_rating from their words). Omit task_id — this is a chat.

"""

    return f"""{header}{voice_hint}

{startup_block}{text}

---
TELEGRAM CONTEXT (read carefully):

This message came from Telegram. The user's chat_id is {chat_id}.
You ALREADY have the chat_id. Do NOT look it up. Do NOT call getUpdates.

RULES:
- NEVER call api.telegram.org directly. You do NOT have the bot token.
- Your plain text reply is AUTOMATICALLY forwarded to Telegram. No action needed for text.
- If you call notify_user for this conversation, set target_channel="telegram".
- To send files/voice/photos/videos, use the Orchestrator Telegram API below.
- If the user asks you to send, upload, attach, share, or show a file
  (German examples: "schick", "sende", "Datei", "PDF", "MP3", "Podcast",
  "Folge", "Download"), you MUST deliver the file as an attachment with the
  Telegram API. Do not only describe the file or mention its path. If the file
  already exists, find the best matching/newest file under /workspace/transfer/
  and send it. If there is no matching file, say clearly where you searched.
- To DOWNLOAD a file the user sent you: you get a `file_id` in the header above —
  pass it to the get-file endpoint below. Do NOT try to download from Telegram directly.
- PHOTOS the user sends are attached to this message and shown to you directly —
  just look at the image and describe/analyze it. No download needed.
- To SEE any other image (one you downloaded, or an image URL), call the `view_image`
  tool (path / file_id / url). Never use OCR or `strings` — you have real vision.
- VOICE messages are already transcribed for you — the transcript is in the message
  text above. Just respond to it. Never download or transcribe audio yourself.
- You have FULL access to all your MCP tools (memory, todos, notifications, orchestrator).
  Use them exactly as you would for Web UI messages! Save memories, create/update TODOs,
  read knowledge.md, and use notify_user — Telegram is just another input channel.
- If unsure about context: READ /workspace/knowledge.md — it has your role, patterns, and learnings.

ORCHESTRATOR TELEGRAM API (use these curl commands):

IMPORTANT — File sending: ALWAYS use the multipart-upload endpoints below (no base64!).
They support up to 50 MB and work for any file size.

Send a document/file to the user:
  curl -X POST {api_base}/send-document-upload {auth} \\
    -F "chat_id={chat_id}" \\
    -F "file=@/path/to/file;filename=report.pdf"

Send an MP3/audio file (shows Telegram audio player with title):
  curl -X POST {api_base}/send-audio-upload {auth} \\
    -F "chat_id={chat_id}" \\
    -F "file=@/path/to/podcast.mp3;filename=podcast.mp3" \\
    -F "title=Morgen-Podcast" \\
    -F "performer=AI Agent"

Send a voice message (MUST be OGG OPUS — convert with ffmpeg first):
  ffmpeg -i input.mp3 -c:a libopus -b:a 64k output.ogg
  curl -X POST {api_base}/send-voice-upload {auth} \\
    -F "chat_id={chat_id}" \\
    -F "file=@output.ogg"

Send a photo (from file):
  curl -X POST {api_base}/send-photo-upload {auth} \\
    -F "chat_id={chat_id}" \\
    -F "file=@/path/to/image.jpg"

Send a photo (from URL — still use JSON for URL-only):
  curl -X POST {api_base}/send-photo {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "photo_url": "https://example.com/img.jpg"}}'

Send a video:
  curl -X POST {api_base}/send-video-upload {auth} \\
    -F "chat_id={chat_id}" \\
    -F "file=@/path/to/video.mp4;filename=video.mp4"

Send a text message with inline keyboard:
  curl -X POST {api_base}/send-message {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "text": "Choose:", "reply_markup": {{"inline_keyboard": [[{{"text": "A", "callback_data": "a"}}, {{"text": "B", "callback_data": "b"}}]]}}}}'

Set bot menu commands:
  curl -X POST {api_base}/set-commands {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"commands": [{{"command": "help", "description": "Hilfe"}}, {{"command": "status", "description": "Status"}}]}}'

Set bot description:
  curl -X POST {api_base}/set-description {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"description": "Dein KI-Assistent", "short_description": "KI Assistent"}}'

Download a file the user sent you (use the file_id from the header above):
  curl -X POST {api_base}/get-file {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"file_id": "THE_FILE_ID"}}'
  # → returns {{"filename": "...", "size": N, "file_base64": "..."}}
  # Save it, e.g.:
  #   curl -s -X POST {api_base}/get-file {auth} -H 'Content-Type: application/json' \\
  #     -d '{{"file_id": "THE_FILE_ID"}}' \\
  #     | python3 -c 'import sys,json,base64; d=json.load(sys.stdin); open("/workspace/"+d["filename"],"wb").write(base64.b64decode(d["file_base64"])); print("saved", d["filename"])'

Send a rich message (Telegram Bot API 10.1 — headings, tables, LaTeX, checklists, maps, audio):
  Pass CommonMark markdown in the "markdown" field — Telegram renders it natively with
  headings, tables, code blocks, checkboxes, LaTeX math, etc.
  curl -X POST {api_base}/send-rich-message {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "markdown": "## My Heading\\n\\nBody text here.\\n\\n| Col A | Col B |\\n|-------|-------|\\n| 1     | 2     |\\n\\n- [x] Done\\n- [ ] Todo\\n\\n$$E=mc^2$$"}}'

  Stream partial rich message (progressive render while building content):
  curl -X POST {api_base}/send-rich-message-draft {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "markdown": "## Draft heading\\n\\nContent so far..."}}'

  You can also pass raw Telegram HTML via the "html" field instead of "markdown".
  Supported markdown features: headings (##), tables, fenced code, checklists (- [x]),
  LaTeX math ($$...$$), blockquotes, bold, italic, strikethrough, links.

Other endpoints: /send-animation, /send-sticker, /send-location, /send-chat-action, /edit-message, /pin-message, /answer-callback, GET /info, GET /get-commands"""


def _build_channel_prompt(text: str, source: str, is_new_session: bool) -> str:
    """Wrap non-Telegram chat with source/channel context."""
    channel = (source or "webapp").lower()
    if channel in {"ios", "iphone", "ipad"}:
        room = "chat:ios"
        target = "ios"
        label = "iOS app"
    elif channel in {"webapp_voice", "voice"}:
        room = "chat:webapp"
        target = "webapp"
        label = "webapp voice chat"
    else:
        room = "chat:webapp"
        target = "webapp"
        label = "webapp chat"

    if is_new_session:
        startup = (
            "MANDATORY FIRST STEPS (do these BEFORE responding):\n"
            "1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns\n"
            "2. Use brain_search (query relevant to this message) for shared knowledge\n"
            f"3. Use memory_search with a focused query AND room=\"{room}\" unless a project room is more precise\n"
            "4. Use skill_search to check the marketplace for a skill that fits this request.\n"
            "   If one fits, skill_install it and FOLLOW its instructions instead of improvising.\n"
            "5. Use list_todos to check for pending work items\n"
        )
    else:
        startup = (
            "BEFORE responding: use brain_search, memory_search with a relevant room filter, "
            "and skill_search if a marketplace skill might fit.\n"
        )

    return (
        f"[CHAT CHANNEL] This message came from {label}. "
        f"When you call notify_user for this conversation, set target_channel=\"{target}\". "
        "Your normal chat answer is automatically returned to the same channel.\n\n"
        f"{startup}"
        "FILE DELIVERY RULE: If the user asks you to send, upload, attach, share, "
        "open, download, or show a file (German examples: 'schick', 'sende', "
        "'Datei', 'PDF', 'MP3', 'Podcast', 'Folge', 'Download'), you MUST deliver "
        "the file as a chat attachment. If the file already exists, find the best "
        "matching/newest file under /workspace/transfer/ and call present_file "
        "with that path. Do not only describe the file or mention its path. If no "
        "matching file exists, say clearly where you searched.\n"
        "AFTER responding: if you learned something new, use memory_save with "
        f"category='learning', room=\"{room}\" (or a project room), and useful tags.\n"
        "AFTER the user gives feedback: if you used a skill, call skill_rate with their rating "
        "(omit task_id — this is a chat).\n\n"
        "Then respond to:\n\n"
        f"{text}"
    )


class ChatConsumer:
    """Consumes chat messages from the Redis queue and processes them via
    per-channel ChatHandler instances.

    Each source channel (ios, telegram:<chat_id>, webapp:<session_id>) gets its
    own ChatHandler with an independent Claude Code session. Handlers resume via
    --resume after agent restarts (session IDs are persisted in Redis for 7 days).

    Live steering: messages that arrive while a handler is responding are folded
    into the running conversation for that same channel only.
    """

    _CLAUDE_SESSION_TTL = 86400 * 7  # 7 days

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:chat"
        self.cancel_channel = f"agent:{agent_id}:chat:cancel"
        self.running = True
        self._handlers: dict[str, object] = {}   # source_key → handler
        self._active_source_key: str | None = None
        self._cancel_listener_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Source-key routing                                                   #
    # ------------------------------------------------------------------ #

    def _source_key(self, source: str, chat_session_id: str | None, telegram_ctx: dict | None) -> str:
        """Derive a stable per-channel routing key."""
        if telegram_ctx:
            return f"telegram:{telegram_ctx.get('chat_id', 'unknown')}"
        if source in ("ios", "iphone", "ipad"):
            return "ios"
        if source in ("webapp_voice", "voice"):
            return f"voice:{chat_session_id or 'default'}"
        if source == "scheduler":
            return "scheduler"
        return f"webapp:{chat_session_id or 'default'}"

    # ------------------------------------------------------------------ #
    # Handler lifecycle                                                    #
    # ------------------------------------------------------------------ #

    async def _get_or_create_handler(self, source_key: str) -> object:
        """Return the handler for this channel, creating and restoring it if needed."""
        if source_key in self._handlers:
            return self._handlers[source_key]

        log_publisher = LogPublisher(self.redis, self.agent_id)
        if settings.agent_mode == "custom_llm":
            from app.llm_chat_handler import LLMChatHandler
            handler = LLMChatHandler(log_publisher)
        elif settings.agent_mode == "codex_cli":
            from app.codex_runner import CodexChatHandler
            handler = CodexChatHandler(log_publisher)
        else:
            from app.chat_handler import ChatHandler
            handler = ChatHandler(log_publisher)

        # Restore persisted Claude session so --resume works after restarts
        stored = await self.redis.get(f"agent:{self.agent_id}:claude_session:{source_key}")
        if stored and hasattr(handler, "session_id"):
            handler.session_id = stored.decode() if isinstance(stored, bytes) else stored
            logger.info("Restored Claude session %s for %s", handler.session_id, source_key)

        self._handlers[source_key] = handler
        return handler

    async def _persist_session(self, source_key: str, handler: object) -> None:
        """Save the handler's Claude session ID to Redis."""
        session_id = getattr(handler, "session_id", None)
        if session_id:
            await self.redis.setex(
                f"agent:{self.agent_id}:claude_session:{source_key}",
                self._CLAUDE_SESSION_TTL,
                session_id,
            )

    async def _reset_handler(self, source_key: str) -> None:
        """Clear session for one channel (new chat)."""
        handler = self._handlers.get(source_key)
        if handler and hasattr(handler, "reset_session"):
            await handler.reset_session()
        await self.redis.delete(f"agent:{self.agent_id}:claude_session:{source_key}")

    # ------------------------------------------------------------------ #
    # Cancel listener                                                      #
    # ------------------------------------------------------------------ #

    async def _listen_for_cancel(self) -> None:
        """Stop whichever channel is currently processing."""
        cancel_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = cancel_redis.pubsub()
        await pubsub.subscribe(self.cancel_channel)
        try:
            while self.running:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    if self._active_source_key:
                        handler = self._handlers.get(self._active_source_key)
                        if handler and getattr(handler, "is_running", False):
                            await handler.stop_current()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            await pubsub.unsubscribe(self.cancel_channel)
            await pubsub.aclose()
            await cancel_redis.aclose()

    # ------------------------------------------------------------------ #
    # Message preparation                                                  #
    # ------------------------------------------------------------------ #

    async def _drain_pending(self, source_key: str) -> list[str]:
        """Pop queued messages for this channel; re-queue messages from other channels."""
        texts: list[str] = []
        requeue: list[bytes] = []
        if not self.redis:
            return texts
        while True:
            raw = await self.redis.rpop(self.queue_name)
            if raw is None:
                break
            qmsg = json.loads(raw)
            qkey = self._source_key(
                qmsg.get("source", "webapp"),
                qmsg.get("chat_session_id"),
                qmsg.get("telegram"),
            )
            if qkey != source_key:
                requeue.append(raw)
                continue
            if qmsg.get("text", "").strip() == "/reset":
                await self._reset_handler(source_key)
                texts.clear()
                continue
            handler = self._handlers.get(source_key)
            texts.append(self._prepare_text(
                qmsg["text"], qmsg.get("telegram"), qmsg.get("source", "webapp"),
                handler,
            ))
        for msg in requeue:
            await self.redis.rpush(self.queue_name, msg)
        return texts

    def _is_new_session(self, handler: object) -> bool:
        if hasattr(handler, "session_id"):
            return handler.session_id is None
        return True

    def _prepare_text(self, text: str, telegram_ctx: dict | None, source: str, handler: object) -> str:
        from app.runner_hooks import get_approval_rules_prefix
        rules_prefix = get_approval_rules_prefix()
        is_new = self._is_new_session(handler)
        if telegram_ctx:
            return rules_prefix + _build_telegram_prompt(text, telegram_ctx, is_new_session=is_new)
        return rules_prefix + _build_channel_prompt(text, source, is_new)

    def _save_images(self, message_id: str, images: list[dict]) -> list[str]:
        """Decode base64 images to workspace files (for the CLI handler).

        Returns the saved file paths. Best-effort — failures are skipped.
        """
        import base64
        import os

        ext_map = {
            "image/jpeg": "jpg", "image/png": "png",
            "image/gif": "gif", "image/webp": "webp",
        }
        out_dir = os.path.join(settings.workspace_dir, ".telegram_images")
        saved: list[str] = []
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return saved
        safe_id = message_id.replace("/", "_")
        for i, img in enumerate(images):
            data = img.get("data")
            if not data:
                continue
            ext = ext_map.get(img.get("media_type", ""), "jpg")
            path = os.path.join(out_dir, f"{safe_id}_{i}.{ext}")
            try:
                with open(path, "wb") as f:
                    f.write(base64.b64decode(data))
                saved.append(path)
            except Exception:
                continue
        return saved

    async def start(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        log_publisher = LogPublisher(self.redis, self.agent_id)

        # Start cancel listener in background
        self._cancel_listener_task = asyncio.create_task(self._listen_for_cancel())

        while self.running:
            try:
                # BRPOP blocks until a message is available (timeout 5s)
                result = await self.redis.brpop(self.queue_name, timeout=5)
                if result is None:
                    continue

                _, msg_json = result
                msg = json.loads(msg_json)
                message_id = msg["id"]
                text = msg["text"]
                model = msg.get("model")
                telegram_ctx = msg.get("telegram")
                source = msg.get("source", "telegram" if telegram_ctx else "webapp")
                chat_session_id = msg.get("chat_session_id")
                images = msg.get("images") or None

                # Route to the correct per-channel handler
                source_key = self._source_key(source, chat_session_id, telegram_ctx)
                handler = await self._get_or_create_handler(source_key)

                # Handle special commands
                if text.strip() == "/reset":
                    await self._reset_handler(source_key)
                    continue

                text = self._prepare_text(text, telegram_ctx, source, handler)

                # Images: the custom-LLM handler sees them natively. The
                # Claude Code CLI handler can't take inline images, so save
                # them to the workspace and point the agent at the files.
                handle_kwargs: dict = {}
                if images:
                    if settings.agent_mode == "custom_llm":
                        handle_kwargs["images"] = images
                    else:
                        saved = self._save_images(message_id, images)
                        if saved:
                            text += (
                                "\n\n[Attached image(s) saved to the workspace — "
                                "use the Read tool to view them:]\n"
                                + "\n".join(saved)
                            )

                # Mark as working while processing chat
                self._active_source_key = source_key
                await log_publisher.publish_status("working", f"chat:{message_id}")

                # Live steering: fold newly-arrived messages from the same channel
                if hasattr(handler, "pending_drain"):
                    handler.pending_drain = lambda: self._drain_pending(source_key)

                timeout = _chat_turn_timeout()
                try:
                    await asyncio.wait_for(
                        handler.handle_message(
                            message_id=message_id,
                            text=text,
                            model=model,
                            **handle_kwargs,
                        ),
                        timeout=timeout,
                    )
                    # Persist Claude session ID so we can --resume after restart
                    await self._persist_session(source_key, handler)
                except asyncio.TimeoutError:
                    logger.error(
                        "Chat turn %s timed out after %ss — aborting",
                        message_id, timeout,
                    )
                    try:
                        if hasattr(handler, "stop_current"):
                            await handler.stop_current()
                    except Exception:  # noqa: BLE001
                        pass
                    await log_publisher.publish_chat(
                        message_id, "error",
                        {"message": "Die Antwort hat zu lange gedauert und "
                                    "wurde abgebrochen. Bitte erneut versuchen."},
                    )
                    await log_publisher.publish_chat(
                        message_id, "done", {"status": "timeout"}
                    )
                finally:
                    self._active_source_key = None
                    await log_publisher.publish_status("idle")

            except aioredis.TimeoutError:
                continue
            except aioredis.ConnectionError:
                await asyncio.sleep(2)
            except Exception as e:
                if self.redis:
                    try:
                        await LogPublisher(self.redis, self.agent_id).publish_chat(
                            "", "error", {"message": f"Chat error: {e}"}
                        )
                    except Exception:
                        pass
                await asyncio.sleep(1)

    async def stop(self) -> None:
        self.running = False
        if self._cancel_listener_task:
            self._cancel_listener_task.cancel()
            try:
                await self._cancel_listener_task
            except asyncio.CancelledError:
                pass
        if self.redis:
            await self.redis.aclose()
