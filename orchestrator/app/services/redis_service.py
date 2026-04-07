import logging
import os

import redis.asyncio as aioredis
from redis.asyncio.sentinel import Sentinel

logger = logging.getLogger(__name__)


class RedisService:
    """Manages Redis connections for pub/sub and task queues.

    Supports two modes:
      - **Standalone** (default): connects via REDIS_URL
      - **Sentinel HA**: set REDIS_SENTINEL_URL=host1:26379,host2:26379,host3:26379
        and REDIS_SENTINEL_MASTER=mymaster to enable automatic failover.
    """

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client: aioredis.Redis | None = None
        self._sentinel: Sentinel | None = None

    async def connect(self) -> None:
        sentinel_url = os.environ.get("REDIS_SENTINEL_URL", "").strip()

        if sentinel_url:
            # Sentinel mode.
            # Accepted formats:
            #   redis+sentinel://host1:26379,host2:26379,host3:26379/mymaster
            #   host1:26379,host2:26379  (plain, master defaults to "mymaster")
            master_name = os.environ.get("REDIS_SENTINEL_MASTER", "mymaster")

            # Strip protocol prefix if present
            raw = sentinel_url
            if "://" in raw:
                raw = raw.split("://", 1)[1]

            # Extract master name from path (e.g. ".../mymaster")
            if "/" in raw:
                raw, master_name = raw.rsplit("/", 1)

            sentinels = []
            for entry in raw.split(","):
                entry = entry.strip()
                if not entry:
                    continue
                if ":" in entry:
                    host, port = entry.rsplit(":", 1)
                    sentinels.append((host, int(port)))
                else:
                    sentinels.append((entry, 26379))

            self._sentinel = Sentinel(sentinels, decode_responses=True)
            self.client = self._sentinel.master_for(master_name)
            await self.client.ping()
            logger.info(
                f"Connected to Redis via Sentinel "
                f"({len(sentinels)} sentinels, master={master_name})"
            )
        else:
            # Standalone mode
            self.client = aioredis.from_url(self.redis_url, decode_responses=True)
            await self.client.ping()

    async def disconnect(self) -> None:
        if self.client:
            await self.client.aclose()

    async def get_agent_status(self, agent_id: str) -> dict:
        if not self.client:
            return {}
        data = await self.client.hgetall(f"agent:{agent_id}:status")
        return data

    MAX_QUEUE_SIZE = 100  # Backpressure: auto-evict oldest tasks beyond this depth

    async def push_task(self, agent_id: str, task_payload: str) -> None:
        if not self.client:
            raise RuntimeError("Redis not connected")
        queue_key = f"agent:{agent_id}:tasks"
        # Push the new task
        await self.client.lpush(queue_key, task_payload)
        # Auto-trim: keep only the newest MAX_QUEUE_SIZE tasks (FIFO rollover).
        # LTRIM keeps indices 0..N-1 (newest first since we LPUSH).
        depth = await self.client.llen(queue_key)
        if depth > self.MAX_QUEUE_SIZE:
            evicted = depth - self.MAX_QUEUE_SIZE
            await self.client.ltrim(queue_key, 0, self.MAX_QUEUE_SIZE - 1)
            import logging
            logging.getLogger(__name__).warning(
                f"Queue {queue_key} exceeded {self.MAX_QUEUE_SIZE} — "
                f"evicted {evicted} oldest task(s)"
            )

    async def get_queue_depth(self, agent_id: str) -> int:
        if not self.client:
            return 0
        return await self.client.llen(f"agent:{agent_id}:tasks")

    async def subscribe(self, channel: str):
        if not self.client:
            raise RuntimeError("Redis not connected")
        pubsub = self.client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub
