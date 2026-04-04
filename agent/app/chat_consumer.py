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

    api_base = f"{orch_url}/api/v1/telegram"
    auth = f"-H 'X-Agent-ID: {agent_id}' -H 'Authorization: Bearer {agent_token}'"

    # Session startup instructions — read knowledge + memories FIRST
    startup_block = ""
    if is_new_session:
        startup_block = """
FIRST STEPS (do these BEFORE responding to the user):
1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns
2. Use knowledge_search (query relevant to this message) to check the shared knowledge base
3. Use memory_search (query: "") to check for recent memories and user preferences
4. Use list_todos to check for pending work items
Then respond to the user's message below with full context.

"""
    else:
        startup_block = """
BEFORE responding: use knowledge_search and memory_search to check for relevant context.
AFTER responding: if you learned something new, use memory_save (category: 'learning').

"""

    return f"""{header}

{startup_block}{text}

---
TELEGRAM CONTEXT (read carefully):

This message came from Telegram. The user's chat_id is {chat_id}.
You ALREADY have the chat_id. Do NOT look it up. Do NOT call getUpdates.

RULES:
- NEVER call api.telegram.org directly. You do NOT have the bot token.
- Your plain text reply is AUTOMATICALLY forwarded to Telegram. No action needed for text.
- To send files/voice/photos/videos, use the Orchestrator Telegram API below.
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

Other endpoints: /send-animation, /send-sticker, /send-location, /send-chat-action, /edit-message, /pin-message, /answer-callback, GET /info, GET /get-commands"""


class ChatConsumer:
    """Consumes chat messages from Redis queue and processes them via ChatHandler or LLMChatHandler.

    Supports message interruption: if a new message arrives while the agent is
    working, the current CLI process is interrupted (SIGINT) and the new message
    is processed immediately. The --resume flag preserves conversation context.
    """

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:chat"
        self.cancel_channel = f"agent:{agent_id}:chat:cancel"
        self.running = True
        self._handler = None  # ChatHandler or LLMChatHandler
        self._cancel_listener_task: asyncio.Task | None = None
        self._queue_watcher_task: asyncio.Task | None = None
        self._interrupt_event = asyncio.Event()

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

    async def _watch_queue_for_interrupt(self) -> None:
        """Monitor queue while handler is busy.

        When a new message arrives, send SIGINT to the Claude CLI process.
        SIGINT makes Claude CLI finish the CURRENT turn gracefully (no lost
        output), then exit. The queued messages are processed as a follow-up
        with --resume to preserve conversation context.

        This means: the agent sees new messages after the current turn,
        not after ALL turns of a multi-turn task.
        """
        watch_redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        try:
            while self.running:
                queue_len = await watch_redis.llen(self.queue_name)
                if queue_len > 0 and self._handler and self._handler.is_running:
                    # New message — finish current turn, then handle it
                    self._interrupt_event.set()
                    await self._handler.stop_current()  # SIGINT → finishes current turn
                    return
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            await watch_redis.aclose()

    def _is_new_session(self) -> bool:
        """Check if the handler has no active session (first message)."""
        if self._handler and hasattr(self._handler, "session_id"):
            return self._handler.session_id is None
        return True

    def _prepare_text(self, text: str, telegram_ctx: dict | None) -> str:
        """Prepare message text, adding Telegram context if present."""
        # Approval rules apply to all messages
        from app.agent_runner import _get_approval_rules_prefix
        rules_prefix = _get_approval_rules_prefix()
        if telegram_ctx:
            return rules_prefix + _build_telegram_prompt(text, telegram_ctx, is_new_session=self._is_new_session())
        # For Web UI chat: also add startup instructions for new sessions
        if self._is_new_session():
            return rules_prefix + (
                "MANDATORY FIRST STEPS (do these BEFORE responding):\n"
                "1. Read /workspace/knowledge.md to recall your role, skills, and learned patterns\n"
                "2. Use knowledge_search (query relevant to this message) for shared knowledge\n"
                "3. Use memory_search (query: '') for recent memories and user preferences\n"
                "4. Use list_todos to check for pending work items\n"
                "AFTER responding: if you learned something new or the user corrected you,\n"
                "use memory_save (category: 'learning') to remember it for next time.\n"
                "Then respond to:\n\n" + text
            )
        # Resumed session — MUST check knowledge/memory for context
        return rules_prefix + (
            "BEFORE responding: use knowledge_search and memory_search to check for relevant context.\n"
            "AFTER responding: if you learned something new, use memory_save (category: 'learning').\n\n"
            + text
        )

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

                # Handle special commands
                if text.strip() == "/reset":
                    await self._handler.reset_session()
                    continue

                text = self._prepare_text(text, telegram_ctx)

                # Mark as working while processing chat
                await log_publisher.publish_status("working", f"chat:{message_id}")

                # Reset interrupt event
                self._interrupt_event.clear()

                # Start queue watcher to detect new messages while we're busy
                self._queue_watcher_task = asyncio.create_task(
                    self._watch_queue_for_interrupt()
                )

                try:
                    # Process the chat message
                    await self._handler.handle_message(
                        message_id=message_id,
                        text=text,
                        model=model,
                    )
                finally:
                    # Stop queue watcher
                    if self._queue_watcher_task and not self._queue_watcher_task.done():
                        self._queue_watcher_task.cancel()
                        try:
                            await self._queue_watcher_task
                        except asyncio.CancelledError:
                            pass
                    self._queue_watcher_task = None

                    # If we were interrupted, drain all queued messages and
                    # combine them into a single follow-up prompt so the agent
                    # processes everything together (with --resume context).
                    if self._interrupt_event.is_set():
                        combined_texts = []
                        combined_id = None
                        combined_model = model
                        while True:
                            queued = await self.redis.rpop(self.queue_name)
                            if queued is None:
                                break
                            qmsg = json.loads(queued)
                            if qmsg["text"].strip() == "/reset":
                                await self._handler.reset_session()
                                combined_texts.clear()
                                continue
                            qtxt = self._prepare_text(
                                qmsg["text"], qmsg.get("telegram")
                            )
                            combined_texts.append(qtxt)
                            combined_id = combined_id or qmsg["id"]
                            combined_model = qmsg.get("model") or combined_model

                        if combined_texts:
                            # Join all queued messages into one prompt
                            follow_up = "\n\n".join(combined_texts)
                            await log_publisher.publish_chat(
                                combined_id, "system",
                                {"message": "New messages received — continuing with full context..."},
                            )
                            await log_publisher.publish_status(
                                "working", f"chat:{combined_id}"
                            )
                            await self._handler.handle_message(
                                message_id=combined_id,
                                text=follow_up,
                                model=combined_model,
                            )

                    # Back to idle after response
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
        if self._queue_watcher_task:
            self._queue_watcher_task.cancel()
            try:
                await self._queue_watcher_task
            except asyncio.CancelledError:
                pass
        if self.redis:
            await self.redis.aclose()
