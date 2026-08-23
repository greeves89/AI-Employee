"""Chat consumer - listens for chat messages and forwards them to ChatHandler."""

import asyncio
import time
import json
import logging
import os

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher

logger = logging.getLogger(__name__)

DEFAULT_CHAT_TURN_TIMEOUT = 600  # seconds
DEFAULT_CODEX_CHAT_TURN_TIMEOUT = 1800  # seconds


def _max_parallel_chats() -> int:
    """How many chat channels this agent may process concurrently.

    Default 1 = the proven serial behaviour (byte-for-byte the old path). Set
    MAX_PARALLEL_CHATS>1 to let DIFFERENT chat sessions run at the same time in
    one container (each spawns its own claude/codex/custom-LLM turn); the same
    session always stays serial/ordered.
    """
    try:
        return max(1, int(os.getenv("MAX_PARALLEL_CHATS", "1")))
    except (TypeError, ValueError):
        return 1


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
    # Auf DIESE Nachricht kann der Agent reagieren (wenn er es fuer passend haelt).
    msg_ref = tg.get("message_id", "") or 0

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

    # Die vollstaendige API-Referenz wiegt rund neunzig Zeilen. Sie JEDER Nachricht
    # anzuhaengen liess den ohnehin wachsenden --resume-Verlauf schneller an die
    # Laengengrenze stossen, obwohl der Agent sie ab dem zweiten Zug laengst im
    # Verlauf hat. Neue Sitzung: alles. Folgezug: ein kurzer Verweis darauf.
    if not is_new_session:
        return f"""{header}{voice_hint}

{startup_block}{text}

---
TELEGRAM CONTEXT:
Antworte einfach als Text — er geht automatisch an den Nutzer. Die vollstaendige
Orchestrator-Telegram-API (Dateien, Fotos, Sprache, Reaktionen, Tastaturen) steht
am Anfang DIESER Sitzung im Verlauf: dort die genauen curl-Aufrufe nachlesen statt
raten. api.telegram.org rufst du nie direkt auf, den Bot-Token hast du nicht.
Basis-URL: {api_base}
Auth-Header: {auth}"""

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

REACTIONS (optional, use sparingly): You may react to the user's message with a
single emoji — like a colleague would. Do this ONLY when it genuinely fits: a heart
for something kind, a shocked face for bad news, a thumbs-up to acknowledge. The
NORMAL case is NO reaction — do not react to every message, that feels mechanical.
Never use a reaction INSTEAD of an answer.

  curl -X POST {api_base}/react {auth} \\
    -H 'Content-Type: application/json' \\
    -d '{{"chat_id": "{chat_id}", "message_id": {msg_ref}, "emoji": "\U0001F44D"}}'

Allowed emojis only (Telegram rejects others): thumbs up/down, heart, fire, party,
100, clapping, grinning, thinking, screaming, open mouth, crying, poo, folded hands,
ok hand, eyes, rocket, biceps, tears of joy, writing hand, smiling-with-hearts.
Send an empty emoji to remove a reaction.

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
        self._active_source_keys: set[str] = set()  # keys currently processing (>1 = parallel)
        self._cancel_listener_task: asyncio.Task | None = None
        # Parallel mode (opt-in via MAX_PARALLEL_CHATS): one lane (asyncio.Queue)
        # per source_key; same channel stays serial, different channels run
        # concurrently up to the semaphore. Empty in serial mode.
        self._lanes: dict[str, asyncio.Queue] = {}
        self._lane_tasks: dict[str, asyncio.Task] = {}
        self._sem: asyncio.Semaphore | None = None

    # ------------------------------------------------------------------ #
    # Source-key routing                                                   #
    # ------------------------------------------------------------------ #

    def _busy_chat_sessions(self) -> list[str]:
        """The currently-processing chat sessions as "chat:<session_id>" — one per
        parallel webapp/voice channel, so the UI marks every busy conversation."""
        out: list[str] = []
        for k in self._active_source_keys:
            if k.startswith("webapp:") or k.startswith("voice:"):
                sid = k.split(":", 1)[1]
                if sid and sid != "default":
                    out.append(f"chat:{sid}")
        return out

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

    async def _get_or_create_handler(
        self, source_key: str, model: str | None = None, log_publisher: LogPublisher | None = None
    ) -> object:
        """Return the handler for this channel, creating and restoring it if needed.

        ``model`` is the model this turn will actually run under. A persisted
        --resume session created under a DIFFERENT model is not restored — resuming
        a CLI session while forcing a different model can hang the turn (no timeout
        short of the 600s watchdog catches it). We drop the stale pointer instead and
        start a fresh session under the new model.

        ``log_publisher`` MUST be the caller's own publisher instance (the one the
        idle watchdog reads `last_activity_at` off), not a fresh one — a handler
        that heartbeats into a different instance is invisible to the watchdog and
        gets killed at the hard 600s ceiling regardless of how active the turn is
        (issue #569). Falling back to a throwaway instance only when the caller
        doesn't pass one keeps this method usable from non-watchdog contexts.
        """
        if source_key in self._handlers:
            return self._handlers[source_key]

        log_publisher = log_publisher or LogPublisher(self.redis, self.agent_id)
        if settings.agent_mode == "custom_llm":
            from app.llm_chat_handler import LLMChatHandler
            handler = LLMChatHandler(log_publisher)
        elif settings.agent_mode == "codex_cli":
            from app.codex_runner import CodexChatHandler
            handler = CodexChatHandler(log_publisher)
        else:
            from app.chat_handler import ChatHandler
            handler = ChatHandler(log_publisher)

        # Restore persisted session so --resume works after restarts — unless the
        # agent's model changed since it was saved.
        key = f"agent:{self.agent_id}:claude_session:{source_key}"
        stored = await self.redis.get(key)
        if stored and hasattr(handler, "session_id"):
            stored = stored.decode() if isinstance(stored, bytes) else stored
            session_id, stored_model = stored, None
            try:
                parsed = json.loads(stored)
                if isinstance(parsed, dict):
                    session_id, stored_model = parsed.get("session_id"), parsed.get("model")
            except (TypeError, ValueError):
                pass  # legacy bare-string value (pre-model-tracking) — no model to compare
            if stored_model and stored_model != model:
                logger.info(
                    "Model changed for %s (%s -> %s) — dropping stale resume session %s",
                    source_key, stored_model, model, session_id,
                )
                await self.redis.delete(key)
            elif session_id:
                handler.session_id = session_id
                logger.info("Restored Claude session %s for %s", handler.session_id, source_key)

        self._handlers[source_key] = handler
        return handler

    async def _persist_session(self, source_key: str, handler: object, model: str | None = None) -> None:
        """Save the handler's session ID (+ the model it ran under) to Redis."""
        session_id = getattr(handler, "session_id", None)
        if session_id:
            await self.redis.setex(
                f"agent:{self.agent_id}:claude_session:{source_key}",
                self._CLAUDE_SESSION_TTL,
                json.dumps({"session_id": session_id, "model": model}),
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
                    # Stop whichever channel(s) are currently processing. In serial
                    # mode that's at most one; in parallel mode a global cancel
                    # stops all in-flight turns (the cancel channel carries no key).
                    for sk in list(self._active_source_keys):
                        handler = self._handlers.get(sk)
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
        lane = self._lanes.get(source_key)
        if lane is not None:
            # Parallel mode: same-channel messages wait in THIS lane (the main
            # loop is the only Redis consumer). Drain them without touching Redis —
            # they are all this channel, so nothing needs re-queueing.
            while not lane.empty():
                try:
                    qmsg = lane.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if qmsg.get("text", "").strip() == "/reset":
                    await self._reset_handler(source_key)
                    texts.clear()
                    continue
                handler = self._handlers.get(source_key)
                texts.append(self._prepare_text(
                    qmsg["text"], qmsg.get("telegram"),
                    qmsg.get("source", "webapp"), handler,
                ))
            return texts
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

    def _skills_prefix(self, text: str) -> str:
        # custom_llm builds its own skills/marketplace context into the system
        # prompt on the first message (see LLMChatHandler). The CLI-based chat
        # handlers (claude_code / codex_cli) had NO such injection at all — the
        # agent only saw a soft "use skill_search" hint and regularly skipped it
        # (#468). Inject the same context CLI tasks already get, once per session.
        if settings.agent_mode == "custom_llm":
            return ""
        from app.runner_hooks import get_marketplace_skill_suggestions, get_skills_context
        return get_skills_context() + get_marketplace_skill_suggestions(text[:200])

    def _wrap(self, text: str, telegram_ctx: dict | None, source: str, is_new: bool) -> str:
        if telegram_ctx:
            return _build_telegram_prompt(text, telegram_ctx, is_new_session=is_new)
        return _build_channel_prompt(text, source, is_new)

    def _prepare_text(self, text: str, telegram_ctx: dict | None, source: str, handler: object) -> str:
        from app.runner_hooks import get_approval_rules_prefix
        is_new = self._is_new_session(handler)
        # Die Autonomie-Regeln standen bisher vor JEDER Nachricht — obwohl sie sich
        # zwischen zwei Nachrichten fast nie aendern und im Verlauf laengst stehen.
        # Jetzt: zum Sitzungsbeginn, und danach nur, wenn der Nutzer sie wirklich
        # geaendert hat (sonst wuesste der Agent von der Aenderung nichts).
        #
        # Der Merker gehoert an den HANDLER, nicht an den Consumer: ein Consumer
        # bedient mehrere Sitzungen gleichzeitig (self._handlers, je source_key).
        # Lag er am Consumer, genuegte eine zweite Sitzung, die die neuen Regeln
        # abholt, damit die erste sie nie zu sehen bekam — sie haette mit einer
        # zurueckgezogenen Freigabe weitergearbeitet.
        rules_prefix = get_approval_rules_prefix()
        if is_new or rules_prefix != getattr(handler, "_last_rules_prefix", None):
            # Ohne Handler (erste Nachricht eines Kanals) gibt es nichts zu merken —
            # is_new ist dann ohnehin wahr, die Regeln gehen also raus.
            if handler is not None:
                handler._last_rules_prefix = rules_prefix
        else:
            rules_prefix = ""
        skills_prefix = self._skills_prefix(text) if is_new else ""
        return rules_prefix + skills_prefix + self._wrap(text, telegram_ctx, source, is_new)

    def _fresh_session_text(self, text: str, telegram_ctx: dict | None, source: str) -> str:
        """Dieselbe Nachricht, aber als ERSTE einer neuen Sitzung aufbereitet.

        Wird gebraucht, wenn ein Zug am zu langen Verlauf scheitert: der Handler
        wirft die Sitzung weg und wiederholt. Ohne diese Fassung liefe er mit dem
        Folgezug-Prompt weiter — der die Regeln, den Skills-Block und die volle
        Telegram-Referenz weglaesst und auf einen Verlauf verweist, den die neue
        Sitzung gar nicht hat.
        """
        from app.runner_hooks import get_approval_rules_prefix
        return (
            get_approval_rules_prefix()
            + self._skills_prefix(text)
            + self._wrap(text, telegram_ctx, source, True)
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

        # Start cancel listener in background
        self._cancel_listener_task = asyncio.create_task(self._listen_for_cancel())

        parallel = _max_parallel_chats()
        if parallel > 1:
            logger.info("Chat consumer: PARALLEL mode (up to %s concurrent channels)", parallel)
            self._sem = asyncio.Semaphore(parallel)
            await self._run_parallel(log_publisher)
        else:
            await self._run_serial(log_publisher)

    async def _run_serial(self, log_publisher: LogPublisher) -> None:
        """Proven serial path: exactly one chat turn at a time (default)."""
        while self.running:
            try:
                result = await self.redis.brpop(self.queue_name, timeout=5)
                if result is None:
                    continue
                _, msg_json = result
                await self._process_one(json.loads(msg_json), log_publisher)
            except aioredis.TimeoutError:
                continue
            except aioredis.ConnectionError:
                await asyncio.sleep(2)
            except Exception as e:  # noqa: BLE001
                await self._report_loop_error(e)
                await asyncio.sleep(1)

    async def _run_parallel(self, log_publisher: LogPublisher) -> None:
        """Dispatch each message to a per-channel lane. Different channels run
        concurrently (bounded by the semaphore); the SAME channel stays serial
        and ordered. The main loop is the only Redis-queue consumer, so there is
        no rpop/rpush race."""
        while self.running:
            try:
                result = await self.redis.brpop(self.queue_name, timeout=5)
                if result is None:
                    continue
                _, msg_json = result
                msg = json.loads(msg_json)
                sk = self._source_key(
                    msg.get("source", "telegram" if msg.get("telegram") else "webapp"),
                    msg.get("chat_session_id"),
                    msg.get("telegram"),
                )
                lane = self._lanes.get(sk)
                if lane is None:
                    lane = asyncio.Queue()
                    self._lanes[sk] = lane
                    self._lane_tasks[sk] = asyncio.create_task(
                        self._lane_worker(sk, lane, log_publisher)
                    )
                await lane.put(msg)
            except aioredis.TimeoutError:
                continue
            except aioredis.ConnectionError:
                await asyncio.sleep(2)
            except Exception as e:  # noqa: BLE001
                await self._report_loop_error(e)
                await asyncio.sleep(1)

    async def _lane_worker(self, source_key: str, lane: asyncio.Queue, log_publisher: LogPublisher) -> None:
        """Serially process one channel's messages. Lanes persist for the agent
        lifetime (an idle lane just blocks cheaply on an empty queue) — no
        concurrent cleanup, so no lost-message race with the dispatcher."""
        while self.running:
            msg = await lane.get()
            async with self._sem:
                try:
                    await self._process_one(msg, log_publisher)
                except Exception as e:  # noqa: BLE001
                    await self._report_loop_error(e)

    async def _report_loop_error(self, e: Exception) -> None:
        if self.redis:
            try:
                await LogPublisher(self.redis, self.agent_id).publish_chat(
                    "", "error", {"message": f"Chat error: {e}"}
                )
            except Exception:  # noqa: BLE001
                pass

    async def _process_one(self, msg: dict, log_publisher: LogPublisher) -> None:
        """Handle a single chat message end-to-end for its channel. Shared by the
        serial and parallel paths."""
        message_id = msg["id"]
        text = msg["text"]
        model = msg.get("model")
        # The model this turn actually runs under (a per-message override, else the
        # agent's configured default) — resolved once here so the same value gates
        # session-resume AND gets persisted alongside the session id below.
        effective_model = model or settings.default_model
        telegram_ctx = msg.get("telegram")
        source = msg.get("source", "telegram" if telegram_ctx else "webapp")
        chat_session_id = msg.get("chat_session_id")
        images = msg.get("images") or None
        # Per-message reasoning level the user picked in the chat ("" = leave the
        # harness at its default). Already whitelisted by the orchestrator.
        reasoning = msg.get("reasoning") or ""

        # Route to the correct per-channel handler
        source_key = self._source_key(source, chat_session_id, telegram_ctx)

        # Den laufenden Gespraechsfaden bekanntgeben, damit Auftraege, die in
        # diesem Zug vergeben werden, ihn mitfuehren. Sonst meldet der
        # Orchestrator die Fertigstellung spaeter in einen Faden zurueck, den
        # niemand ansieht — fuer den Nutzer sah es aus, als komme nie eine
        # Rueckmeldung, obwohl die Arbeit fertig war.
        from app.tools.api_client import current_chat_session

        current_chat_session.set(chat_session_id)

        handler = await self._get_or_create_handler(source_key, effective_model, log_publisher)

        # Handle special commands
        if text.strip() == "/reset":
            await self._reset_handler(source_key)
            return

        raw_text = text
        text = self._prepare_text(text, telegram_ctx, source, handler)
        # Fallschirm fuer den Laengenfehler: dieselbe Nachricht, aufbereitet als
        # erste einer neuen Sitzung. Der Handler greift nur danach, wenn er den
        # Verlauf tatsaechlich wegwerfen musste.
        fresh_text = self._fresh_session_text(raw_text, telegram_ctx, source)

        # Images: the custom-LLM handler sees them natively. The Claude Code CLI
        # handler can't take inline images, so save them to the workspace and
        # point the agent at the files.
        handle_kwargs: dict = {}
        if images:
            if settings.agent_mode == "custom_llm":
                handle_kwargs["images"] = images
            else:
                saved = self._save_images(message_id, images)
                if saved:
                    suffix = (
                        "\n\n[Attached image(s) saved to the workspace — "
                        "use the Read tool to view them:]\n"
                        + "\n".join(saved)
                    )
                    text += suffix
                    fresh_text += suffix
        if hasattr(handler, "session_id"):
            handler.fresh_session_text = fresh_text

        # Mark as working while processing chat. Report the SESSION id (not the
        # per-message id) so the UI can link the busy pill/rail to the actual
        # conversation, and the FULL set of parallel sessions so every busy chat
        # lights up — not just one.
        self._active_source_keys.add(source_key)
        await log_publisher.publish_status(
            "working",
            f"chat:{chat_session_id}" if chat_session_id else f"chat:{message_id}",
            active_sessions=self._busy_chat_sessions(),
        )

        # Live steering: fold newly-arrived messages from the same channel
        if hasattr(handler, "pending_drain"):
            handler.pending_drain = lambda sk=source_key: self._drain_pending(sk)

        # Stillstands-Wachhund statt harter Gesamtdauer. Vorher wurde nach einer
        # festen Zeit abgebrochen, egal ob der Agent noch arbeitete — bei einem
        # groesseren Umbau ("bau die App um, mach sie mobiltauglich") schlug das
        # mitten in die laufende Arbeit und warf alles weg. Jetzt zaehlt nur, ob
        # ueberhaupt noch etwas passiert: jedes veroeffentlichte Ereignis setzt die
        # Uhr zurueck. Ein wirklich haengender Turn faellt weiterhin raus.
        idle_limit = _chat_turn_timeout()
        # Die Uhr beginnt HIER — nicht irgendwann davor. `last_activity_at` lebt am
        # LogPublisher ueber Turns hinweg; ohne dieses Zuruecksetzen zaehlte die
        # Gespraechspause des Nutzers als Stillstand des Agenten. Wer zehn Minuten
        # nichts schrieb und dann fragte, bekam seine Antwort nach 15 Sekunden mit
        # „hat sich nicht mehr gemeldet" abgebrochen — noch bevor der Agent ueberhaupt
        # etwas tun konnte.
        log_publisher.last_activity_at = time.monotonic()
        turn = asyncio.ensure_future(
            handler.handle_message(
                message_id=message_id,
                text=text,
                model=model,
                reasoning=reasoning,
                **handle_kwargs,
            )
        )
        try:
            while True:
                try:
                    await asyncio.wait_for(asyncio.shield(turn), timeout=15)
                    break
                except asyncio.TimeoutError:
                    quiet = time.monotonic() - getattr(
                        log_publisher, "last_activity_at", time.monotonic()
                    )
                    if quiet >= idle_limit:
                        turn.cancel()
                        raise
                    continue
            # Persist Claude session ID so we can --resume after restart
            await self._persist_session(source_key, handler, effective_model)
        except asyncio.TimeoutError:
            logger.error(
                "Chat turn %s aborted — no activity for %ss (agent appears stuck)",
                message_id, idle_limit,
            )
            try:
                if hasattr(handler, "stop_current"):
                    await handler.stop_current()
            except Exception:  # noqa: BLE001
                pass
            await log_publisher.publish_chat(
                message_id, "error",
                {"message": "Der Agent hat sich zwischendurch nicht mehr gemeldet und "
                            "wurde abgebrochen. Bitte erneut versuchen."},
            )
            await log_publisher.publish_chat(message_id, "done", {"status": "timeout"})
        finally:
            self._active_source_keys.discard(source_key)
            if not self._active_source_keys:
                await log_publisher.publish_status("idle")
            else:
                # Other sessions still running → keep the busy set accurate.
                busy = self._busy_chat_sessions()
                await log_publisher.publish_status(
                    "working", busy[0] if busy else "", active_sessions=busy,
                )

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
