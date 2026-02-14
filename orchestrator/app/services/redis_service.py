import redis.asyncio as aioredis


class RedisService:
    """Manages Redis connections for pub/sub and task queues."""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client: aioredis.Redis | None = None

    async def connect(self) -> None:
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

    async def push_task(self, agent_id: str, task_payload: str) -> None:
        if not self.client:
            raise RuntimeError("Redis not connected")
        await self.client.lpush(f"agent:{agent_id}:tasks", task_payload)

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
