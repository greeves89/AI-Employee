import hashlib
import hmac
import logging
import os
import secrets

import redis.asyncio as aioredis
from redis.asyncio.sentinel import Sentinel

from app.config import settings
from app.core.log_redaction import scrub_log

logger = logging.getLogger(__name__)


def agent_acl_username(agent_id: str) -> str:
    """Redis ACL username for an agent's own scoped connection."""
    return f"agent-{agent_id}"


def agent_acl_password(agent_id: str) -> str:
    """Derive a per-agent Redis ACL password deterministically from api_secret_key.

    Same pattern as make_agent_token() in dependencies.py: no extra secret to
    store or rotate per agent, the orchestrator can always re-derive it (e.g.
    to reconnect after a Redis restart) purely from agent_id + the existing
    server secret. Domain-separated via the "redis-acl:" prefix so this
    password can never collide with the HMAC agent auth token.
    """
    return hmac.new(
        settings.api_secret_key.encode(),
        f"redis-acl:{agent_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


# Redis ACL rule set for a single agent's own connection (part of Sentinel epic
# #588, sub-issue #589). Goal: an agent can fully use its own key/channel space
# and the few global channels/queues the agent code actually publishes to or
# reads from (see agent/app/{log_publisher,message_consumer,task_consumer,
# chat_consumer}.py, agent/app/tools/api_client.py) — but can no longer spoof
# another agent's status/logs/activity, nor run admin-level commands (FLUSHALL,
# CONFIG, ACL, KEYS, MONITOR, ...) against the shared Redis instance. Today
# every agent container connects with the one shared requirepass credential,
# so any agent can currently publish to ANY other agent's agent:{id}:logs
# channel and impersonate it.
#
# `meeting:*:response:{id}` is still "own key space" despite the wildcard:
# only the room_id varies, {id} is always this agent's own id (see
# message_consumer.py: `f"meeting:{room_id}:response:{self.agent_id}"`), so a
# full read/write grant here does not expose another agent's data.
_AGENT_ACL_KEY_PATTERNS = ["agent:{id}:*", "meeting:*:response:{id}"]
_AGENT_ACL_CHANNEL_PATTERNS = [
    "agent:{id}:*",
    "agents:logs:all",
    "chat:completions",
    "agent:messages:persist",
    # task_consumer.py publishes task lifecycle events on these two globals.
    "task:started",
    "task:completions",
]
# Broad read/write/pubsub, explicitly minus admin/dangerous command categories
# (FLUSHALL, FLUSHDB, CONFIG, SHUTDOWN, ACL, MONITOR, KEYS, CLIENT, DEBUG, ...).
_AGENT_ACL_COMMAND_RULES = ["+@read", "+@write", "+@pubsub", "-@admin", "-@dangerous"]

# The inter-agent inbox (message_consumer.py's send_message tool does
# `LPUSH agent:{to_agent_id}:messages`) needs to stay reachable across ALL
# agent ids, not just this agent's own — but granting the broad +@read/+@write
# above on that wildcard would also let any agent LRANGE/LREM/LPOP another
# agent's inbox. Redis 7 ACL selectors scope an independent command+key rule
# alongside the user's root permissions, so this selector grants exactly one
# command (LPUSH) on exactly this pattern, with no read/delete access.
_CROSS_AGENT_INBOX_SELECTOR = "(~agent:*:messages +lpush)"


def build_agent_acl_setuser_args(agent_id: str) -> list[str]:
    """Build the `ACL SETUSER` argument list for one agent's scoped user.

    Split out as a pure function (no I/O) so the exact rule set can be unit
    tested without a live Redis — and so the same args can be pasted into
    `redis-cli ACL SETUSER ...` for a manual live smoke test before this is
    enabled by default (settings.redis_acl_enabled).
    """
    args = ["reset", "on", f">{agent_acl_password(agent_id)}", "resetkeys", "resetchannels"]
    args += [f"~{p.format(id=agent_id)}" for p in _AGENT_ACL_KEY_PATTERNS]
    args += [f"&{p.format(id=agent_id)}" for p in _AGENT_ACL_CHANNEL_PATTERNS]
    args += _AGENT_ACL_COMMAND_RULES
    args.append(_CROSS_AGENT_INBOX_SELECTOR)
    return args


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

    async def ensure_agent_acl_user(self, agent_id: str) -> str:
        """Create/update the least-privilege Redis ACL user for one agent.

        Idempotent (ACL SETUSER replaces the user's rules wholesale each
        call), so this is safe to call on every agent create/start/restart —
        it always converges on build_agent_acl_setuser_args()'s current rule
        set. Returns the scoped REDIS_URL to hand to that agent's container.
        Requires settings.redis_acl_enabled (see config.py for why this
        defaults to off) and an admin-level self.client connection.
        """
        if not self.client:
            raise RuntimeError("Redis not connected")
        username = agent_acl_username(agent_id)
        await self.client.execute_command("ACL", "SETUSER", username, *build_agent_acl_setuser_args(agent_id))
        return self._scoped_url(username, agent_acl_password(agent_id))

    async def revoke_agent_acl_user(self, agent_id: str) -> None:
        """Remove an agent's scoped ACL user (call on agent deletion)."""
        if not self.client:
            return
        username = agent_acl_username(agent_id)
        try:
            await self.client.execute_command("ACL", "DELUSER", username)
        except Exception as e:
            logger.warning("Failed to delete Redis ACL user %s: %s", scrub_log(username), e)

    def _scoped_url(self, username: str, password: str) -> str:
        """Rewrite self.redis_url's auth to the given ACL user, keep host/port/db.

        Raises in Sentinel-HA mode (see connect()): self.redis_url is a fixed
        standalone address, but under REDIS_SENTINEL_URL the actual Redis
        master is discovered dynamically and can move on failover, so baking
        a scoped URL from self.redis_url's host/port would point an agent at
        a stale or wrong node. Per-agent ACL over Sentinel needs its own
        discovery-aware connection string, tracked as a follow-up on #589 —
        until then, the caller (_agent_redis_url in agent_manager.py) lets
        this propagate and fails closed: with redis_acl_enabled explicitly
        on, a security flag that quietly degrades to the shared admin
        credential on error would defeat its own purpose.
        """
        if os.environ.get("REDIS_SENTINEL_URL", "").strip():
            raise NotImplementedError(
                "Per-agent Redis ACL URLs are not yet supported in Sentinel-HA "
                "mode — tracked as a follow-up on #589"
            )
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(self.redis_url)
        netloc = f"{username}:{password}@{parts.hostname}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

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
                f"Queue {scrub_log(queue_key)} exceeded {self.MAX_QUEUE_SIZE} — "
                f"evicted {evicted} oldest task(s)"
            )

    async def get_queue_depth(self, agent_id: str) -> int:
        if not self.client:
            return 0
        return await self.client.llen(f"agent:{agent_id}:tasks")

    # Release only if we still hold the lock (compare-and-delete). Prevents a
    # dispatcher whose TTL already expired from deleting a lock a *different*
    # dispatcher has since acquired.
    _RELEASE_LOCK_SCRIPT = (
        'if redis.call("get", KEYS[1]) == ARGV[1] then '
        'return redis.call("del", KEYS[1]) else return 0 end'
    )

    async def acquire_lock(self, key: str, ttl_seconds: int = 20) -> str | None:
        """Atomically acquire a short-lived named lock (SET NX EX).

        Generic building block behind acquire_dispatch_lock. Returns a token
        to release the lock with, or None if another holder already has it.
        """
        if not self.client:
            return None
        token = secrets.token_hex(8)
        acquired = await self.client.set(f"lock:{key}", token, nx=True, ex=ttl_seconds)
        return token if acquired else None

    async def release_lock(self, key: str, token: str) -> None:
        if not self.client or not token:
            return
        try:
            await self.client.eval(self._RELEASE_LOCK_SCRIPT, 1, f"lock:{key}", token)
        except Exception as e:
            logger.warning("Failed to release lock %s: %s", scrub_log(key), e)

    async def acquire_dispatch_lock(self, agent_id: str, ttl_seconds: int = 20) -> str | None:
        """Atomically acquire a short-lived per-agent task-dispatch lock.

        Guards the "is the agent busy" check immediately before a schedule
        pushes a task, so two schedules due in the same tick (or overlapping
        scheduler ticks) can't both see the agent as free and dispatch on top
        of an already-running task (fixes #548). The short TTL means a crash
        mid-dispatch self-heals within seconds instead of wedging the agent.

        Returns a token to release the lock with, or None if another
        dispatch for this agent is already in flight.
        """
        return await self.acquire_lock(f"agent:{agent_id}:dispatch_lock", ttl_seconds)

    async def release_dispatch_lock(self, agent_id: str, token: str) -> None:
        await self.release_lock(f"agent:{agent_id}:dispatch_lock", token)

    async def subscribe(self, channel: str):
        if not self.client:
            raise RuntimeError("Redis not connected")
        pubsub = self.client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub
