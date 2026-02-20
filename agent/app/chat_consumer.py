"""Chat consumer - listens for chat messages and forwards them to ChatHandler."""

import asyncio
import json

import redis.asyncio as aioredis

from app.config import settings
from app.log_publisher import LogPublisher


class ChatConsumer:
    """Consumes chat messages from Redis queue and processes them via ChatHandler or LLMChatHandler."""

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

                # Handle special commands
                if text.strip() == "/reset":
                    await self._handler.reset_session()
                    continue

                # Mark as working while processing chat
                await log_publisher.publish_status("working", f"chat:{message_id}")

                try:
                    # Process the chat message
                    await self._handler.handle_message(
                        message_id=message_id,
                        text=text,
                        model=model,
                    )
                finally:
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
        if self.redis:
            await self.redis.aclose()
