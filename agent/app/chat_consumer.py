"""Chat consumer - listens for chat messages and forwards them to ChatHandler."""

import asyncio
import json

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher


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
- To send files/voice/photos/videos, use the Orchestrator Telegram API below.
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

Send a document/file to the user:
  curl -X POST {api_base}/send-document {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "document_base64": "'$(base64 -w0 /path/to/file)'", "filename": "report.pdf"}}'

Send a voice message (MUST be OGG OPUS — convert with ffmpeg first):
  ffmpeg -i input.mp3 -c:a libopus -b:a 64k output.ogg
  curl -X POST {api_base}/send-voice {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "voice_base64": "'$(base64 -w0 output.ogg)'"}}'

Send a photo (from file):
  curl -X POST {api_base}/send-photo {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "photo_base64": "'$(base64 -w0 /path/to/image.jpg)'"}}'

Send a photo (from URL):
  curl -X POST {api_base}/send-photo {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "photo_url": "https://example.com/img.jpg"}}'

Send a video:
  curl -X POST {api_base}/send-video {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": {chat_id}, "video_base64": "'$(base64 -w0 /path/to/video.mp4)'"}}'

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

Other endpoints: /send-animation, /send-sticker, /send-location, /send-chat-action, /edit-message, /pin-message, /answer-callback, GET /info, GET /get-commands"""


class ChatConsumer:
    """Consumes chat messages from the Redis queue and processes them via
    ChatHandler / LLMChatHandler.

    Live steering: messages that arrive while the agent is responding are NOT
    interrupted. The handler pulls them in mid-flow (via `pending_drain`) and
    folds them into the SAME conversation — just like a person adding a remark
    while you're still working.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:chat"
        self.cancel_channel = f"agent:{agent_id}:chat:cancel"
        self.running = True
        self._handler = None  # ChatHandler or LLMChatHandler
        self._cancel_listener_task: asyncio.Task | None = None

    async def _listen_for_cancel(self) -> None:
        """Listen on Redis PubSub for cancel signals and stop the handler."""
        cancel_redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = cancel_redis.pubsub()
        await pubsub.subscribe(self.cancel_channel)
        try:
            while self.running:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    if self._handler and self._handler.is_running:
                        await self._handler.stop_current()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            await pubsub.unsubscribe(self.cancel_channel)
            await pubsub.aclose()
            await cancel_redis.aclose()

    async def _drain_pending(self) -> list[str]:
        """Pop every queued chat message (oldest first) and return prepared
        texts. The handler calls this mid-response to fold newly-arrived
        messages into the running conversation. `/reset` is honored inline.
        """
        texts: list[str] = []
        if not self.redis:
            return texts
        while True:
            queued = await self.redis.rpop(self.queue_name)
            if queued is None:
                break
            qmsg = json.loads(queued)
            if qmsg.get("text", "").strip() == "/reset":
                if self._handler:
                    await self._handler.reset_session()
                texts.clear()
                continue
            texts.append(self._prepare_text(qmsg["text"], qmsg.get("telegram")))
        return texts

    def _is_new_session(self) -> bool:
        """Check if the handler has no active session (first message)."""
        if self._handler and hasattr(self._handler, "session_id"):
            return self._handler.session_id is None
        return True

    def _prepare_text(self, text: str, telegram_ctx: dict | None) -> str:
        """Prepare message text, adding Telegram context if present."""
        # Approval rules apply to all messages
        from app.runner_hooks import get_approval_rules_prefix
        rules_prefix = get_approval_rules_prefix()
        if telegram_ctx:
            return rules_prefix + _build_telegram_prompt(text, telegram_ctx, is_new_session=self._is_new_session())
        # For Web UI chat: also add startup instructions for new sessions
        if self._is_new_session():
            return rules_prefix + (
                "MANDATORY FIRST STEPS (do these BEFORE responding):\n"
                "1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns\n"
                "2. Use brain_search (query relevant to this message) for shared knowledge\n"
                "3. Use memory_search with a focused query AND a room filter\n"
                "   (room=\"chat:webui\" for UI chats, or \"project:<name>/<area>\" if the user\n"
                "   is asking about a specific project). Rooms improve retrieval precision massively.\n"
                "4. Use skill_search to check the marketplace for a skill that fits this request.\n"
                "   If one fits, skill_install it and FOLLOW its instructions instead of improvising.\n"
                "5. Use list_todos to check for pending work items\n"
                "AFTER responding: if you learned something new or the user corrected you,\n"
                "use memory_save with category='learning', room=\"chat:webui\" (or project room),\n"
                "tag_type='permanent' (or 'transient' for task state), and tags=['learning', ...].\n"
                "If the server returns a 409 contradiction warning, re-call with override=true\n"
                "only if you're confident the new content should replace the existing one.\n"
                "AFTER the user gives feedback on your result: if you used a marketplace skill,\n"
                "call skill_rate (skill_id, helpfulness 1-5, rating 1-5, and user_rating\n"
                "interpreted from the user's words). Omit task_id — this is a chat.\n"
                "Then respond to:\n\n" + text
            )
        # Resumed session — MUST check knowledge/memory for context
        return rules_prefix + (
            "BEFORE responding: use brain_search and memory_search to check for relevant context,\n"
            "and skill_search — if a marketplace skill fits, skill_install it and follow it.\n"
            "AFTER responding: if you learned something new, use memory_save (category: 'learning').\n"
            "AFTER the user gives feedback: if you used a skill, call skill_rate with their rating\n"
            "(skill_id, helpfulness, rating, user_rating; omit task_id — this is a chat).\n\n"
            + text
        )

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

        # Choose handler based on agent mode
        if settings.agent_mode == "custom_llm":
            from app.llm_chat_handler import LLMChatHandler
            self._handler = LLMChatHandler(log_publisher)
        else:
            from app.chat_handler import ChatHandler
            self._handler = ChatHandler(log_publisher)

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
                images = msg.get("images") or None

                # Handle special commands
                if text.strip() == "/reset":
                    await self._handler.reset_session()
                    continue

                text = self._prepare_text(text, telegram_ctx)

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
                await log_publisher.publish_status("working", f"chat:{message_id}")

                # Live steering: the handler pulls newly-arrived messages into
                # the running conversation via this hook (no interruption).
                if hasattr(self._handler, "pending_drain"):
                    self._handler.pending_drain = self._drain_pending

                try:
                    await self._handler.handle_message(
                        message_id=message_id,
                        text=text,
                        model=model,
                        **handle_kwargs,
                    )
                finally:
                    await log_publisher.publish_status("idle")

            except aioredis.ConnectionError:
                await asyncio.sleep(2)
            except Exception as e:
                if self.redis:
                    try:
                        log_publisher = LogPublisher(self.redis, self.agent_id)
                        await log_publisher.publish_chat(
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
