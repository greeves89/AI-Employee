import json
from datetime import datetime, timezone

import redis.asyncio as aioredis


class LogPublisher:
    """Publishes agent events to Redis PubSub for real-time streaming."""

    def __init__(self, redis: aioredis.Redis, agent_id: str):
        self.redis = redis
        self.agent_id = agent_id

    async def publish(self, task_id: str, event_type: str, data: dict | str) -> None:
        message = json.dumps(
            {
                "agent_id": self.agent_id,
                "task_id": task_id,
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        channel = f"agent:{self.agent_id}:logs"
        await self.redis.publish(channel, message)
        await self.redis.publish("agents:logs:all", message)
        # Store in activity history (keep last 200 events)
        history_key = f"agent:{self.agent_id}:activity"
        await self.redis.rpush(history_key, message)
        await self.redis.ltrim(history_key, -200, -1)

    async def publish_chat(self, message_id: str, event_type: str, data: dict | str) -> None:
        """Publish chat events to a dedicated chat channel."""
        message = json.dumps(
            {
                "agent_id": self.agent_id,
                "message_id": message_id,
                "type": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await self.redis.publish(f"agent:{self.agent_id}:chat:response", message)

    async def publish_status(self, state: str, current_task: str = "") -> None:
        await self.redis.hset(
            f"agent:{self.agent_id}:status",
            mapping={
                "state": state,
                "current_task": current_task,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            },
        )
