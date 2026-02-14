"""Chat consumer - listens for chat messages and forwards them to ChatHandler."""

import asyncio
import json

import redis.asyncio as aioredis

from app.chat_handler import ChatHandler
from app.config import settings
from app.log_publisher import LogPublisher


class ChatConsumer:
    """Consumes chat messages from Redis queue and processes them via ChatHandler."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.redis: aioredis.Redis | None = None
        self.queue_name = f"agent:{agent_id}:chat"
        self.running = True
        self._handler: ChatHandler | None = None

    async def start(self) -> None:
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=False)
        log_publisher = LogPublisher(self.redis, self.agent_id)
        self._handler = ChatHandler(log_publisher)

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

                # Process the chat message
                await self._handler.handle_message(
                    message_id=message_id,
                    text=text,
                    model=model,
                )

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
        if self.redis:
            await self.redis.aclose()
