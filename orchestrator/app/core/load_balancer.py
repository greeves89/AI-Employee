from dataclasses import dataclass, field

from app.services.redis_service import RedisService


@dataclass
class AgentMetrics:
    agent_id: str
    state: str
    queue_depth: int
    cpu_percent: float
    memory_percent: float
    healthy: bool
    category: str = ""  # Agent template category (dev, data, devops, etc.)
    role: str = ""      # Agent role/specialization


class LoadBalancer:
    """Capacity- and capability-aware load balancer for distributing tasks.

    Score formula (lower is better):
        score = (queue_depth * 3) + (cpu_percent * 0.02) + (memory_percent * 0.01)

    Routing priority:
    1. If required_capability is set, filter agents by matching category/role
    2. For urgent tasks (priority >= 3), prefer idle agents
    3. Pick the agent with the lowest load score
    """

    def __init__(self, redis: RedisService):
        self.redis = redis

    async def select_agent(
        self, priority: int = 1, required_capability: str | None = None
    ) -> str | None:
        metrics = await self._collect_metrics()
        available = [
            m for m in metrics
            if m.healthy and m.state not in ("stopped", "error", "unknown")
        ]

        if not available:
            return None

        # Capability matching: filter by category or role keyword
        if required_capability:
            cap = required_capability.lower()
            matched = [
                m for m in available
                if cap in m.category.lower() or cap in m.role.lower()
            ]
            if matched:
                available = matched
            # If no match, fall through to all available (best-effort)

        # For urgent tasks (priority >= 3), prefer idle agents
        if priority >= 3:
            idle = [m for m in available if m.state == "idle"]
            if idle:
                return min(idle, key=self._score).agent_id

        return min(available, key=self._score).agent_id

    @staticmethod
    def _score(m: AgentMetrics) -> float:
        return (m.queue_depth * 3) + (m.cpu_percent * 0.02) + (m.memory_percent * 0.01)

    async def _collect_metrics(self) -> list[AgentMetrics]:
        if not self.redis.client:
            return []

        metrics = []
        cursor = "0"
        while True:
            cursor, keys = await self.redis.client.scan(
                cursor=cursor, match="agent:*:status", count=100
            )
            for key in keys:
                data = await self.redis.client.hgetall(key)
                agent_id = key.split(":")[1]
                queue_depth = await self.redis.get_queue_depth(agent_id)
                metrics.append(
                    AgentMetrics(
                        agent_id=agent_id,
                        state=data.get("state", "unknown"),
                        queue_depth=queue_depth,
                        cpu_percent=float(data.get("cpu_percent", "0")),
                        memory_percent=float(data.get("memory_percent", "0")),
                        healthy=data.get("healthy", "true") == "true",
                        category=data.get("category", ""),
                        role=data.get("role", ""),
                    )
                )
            if cursor == "0" or cursor == 0:
                break

        return metrics
